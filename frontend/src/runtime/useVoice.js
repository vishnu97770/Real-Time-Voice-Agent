// Browser voice I/O: streaming speech recognition (interim + final results),
// sentence-by-sentence speech synthesis, and barge-in.
//
// Barge-in: recognition stays on while the agent is speaking. If the caller
// starts talking, playback is cut immediately and the turn switches to them.
// The browser has no separate VAD signal, so the recognizer's first interim
// result plays that role. Words the agent itself is speaking (picked up by the
// mic) are filtered out so the agent doesn't interrupt itself.

import { useCallback, useEffect, useRef, useState } from "react";
import { splitSentences } from "./sentences.js";

const Recognition =
  typeof window !== "undefined"
    ? window.SpeechRecognition || window.webkitSpeechRecognition
    : undefined;

export const voiceSupport = {
  recognition: Boolean(Recognition),
  synthesis: typeof window !== "undefined" && "speechSynthesis" in window,
};

const BARGE_IN_MIN_WORDS = 2;
const SPEECH_START_TIMEOUT_MS = 4000;

const normalize = (text) => text.toLowerCase().replace(/[^a-z0-9 ]+/g, " ").replace(/\s+/g, " ").trim();

// The voice the operator chose if this browser has it, else one matching the
// browser's language.
function pickVoice(preferredName) {
  const voices = window.speechSynthesis.getVoices();
  const language = (navigator.language || "en-US").toLowerCase();

  return (
    (preferredName && voices.find((voice) => voice.name === preferredName)) ||
    voices.find((voice) => voice.lang.toLowerCase() === language) ||
    voices.find((voice) => voice.lang.toLowerCase().startsWith("en")) ||
    null
  );
}

// The voices this browser can speak with. Browsers load them asynchronously, so
// the list can be empty at first and fill in.
export function useSpeechVoices() {
  const [voices, setVoices] = useState(() =>
    voiceSupport.synthesis ? window.speechSynthesis.getVoices() : []
  );

  useEffect(() => {
    if (!voiceSupport.synthesis) return undefined;

    const synthesis = window.speechSynthesis;
    const update = () => setVoices(synthesis.getVoices());

    synthesis.addEventListener("voiceschanged", update);

    return () => synthesis.removeEventListener("voiceschanged", update);
  }, []);

  return voices;
}

export function useVoice({ onFinal, onBargeIn, voiceName = "" }) {
  const [micState, setMicState] = useState("off"); // off | on | blocked | error
  const [interim, setInterim] = useState("");

  const recognizerRef = useRef(null);
  const wantListeningRef = useRef(false);
  const speakingRef = useRef(false);
  const spokenTextRef = useRef("");
  const activeSpeechRef = useRef(null);
  const onFinalRef = useRef(onFinal);
  const onBargeInRef = useRef(onBargeIn);
  const voiceNameRef = useRef(voiceName);

  useEffect(() => {
    onFinalRef.current = onFinal;
    onBargeInRef.current = onBargeIn;
    voiceNameRef.current = voiceName;
  }, [onFinal, onBargeIn, voiceName]);

  // Resolves the pending speak() promise with false and stops playback.
  const cancelSpeech = useCallback(() => {
    const active = activeSpeechRef.current;

    activeSpeechRef.current = null;
    active?.finish(false);

    if (voiceSupport.synthesis) window.speechSynthesis.cancel();
  }, []);

  // Opens a speech stream. Sentences are pushed as they arrive and start playing
  // straight away, so the agent begins speaking on the first sentence of a reply
  // instead of waiting for the whole thing. `done` resolves true if everything
  // pushed was spoken, false if it was cut short (or the stream was cancelled).
  const openSpeech = useCallback(() => {
    cancelSpeech();

    let settled = false;
    let ended = false;
    let started = false;
    let queued = 0;
    let finished = 0;
    let watchdog = null;
    let resolveDone;

    const done = new Promise((resolve) => {
      resolveDone = resolve;
    });

    const finish = (completed) => {
      if (settled) return;
      settled = true;
      clearTimeout(watchdog);

      if (activeSpeechRef.current?.finish === finish) activeSpeechRef.current = null;

      speakingRef.current = false;
      spokenTextRef.current = "";
      resolveDone(completed);
    };

    const settleIfDrained = () => {
      if (ended && finished >= queued) finish(true);
    };

    activeSpeechRef.current = { finish };
    speakingRef.current = true;
    spokenTextRef.current = "";

    return {
      done,

      push(text) {
        if (settled || !voiceSupport.synthesis) return;

        spokenTextRef.current = `${spokenTextRef.current} ${normalize(text)}`;

        const voice = pickVoice(voiceNameRef.current);

        for (const sentence of splitSentences(text)) {
          const utterance = new SpeechSynthesisUtterance(sentence);

          if (voice) utterance.voice = voice;
          utterance.rate = 1.02;
          utterance.onstart = () => {
            started = true;
            clearTimeout(watchdog);
          };
          utterance.onend = () => {
            finished += 1;
            settleIfDrained();
          };
          // "interrupted" / "canceled" mean we cut it off. Any other error means
          // the engine couldn't speak (e.g. no voices installed): not the caller's
          // doing, so don't report it as a barge-in.
          utterance.onerror = (event) =>
            finish(event.error !== "interrupted" && event.error !== "canceled");

          queued += 1;
          window.speechSynthesis.speak(utterance);
        }

        // Some engines accept speak() and then never fire any event. Don't let
        // the whole call wait on a voice that is never going to start.
        if (!started && !watchdog) {
          watchdog = setTimeout(() => finish(true), SPEECH_START_TIMEOUT_MS);
        }
      },

      end() {
        ended = true;
        settleIfDrained();
      },
    };
  }, [cancelSpeech]);

  const isEcho = useCallback(
    (heard) => {
      const value = normalize(heard);

      return Boolean(value) && speakingRef.current && spokenTextRef.current.includes(value);
    },
    []
  );

  const startListening = useCallback(() => {
    if (!Recognition) return false;

    wantListeningRef.current = true;

    if (recognizerRef.current) return true;

    const recognizer = new Recognition();

    recognizer.continuous = true;
    recognizer.interimResults = true;
    recognizer.lang = navigator.language || "en-US";

    recognizer.onstart = () => setMicState("on");

    recognizer.onresult = (event) => {
      let interimText = "";

      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const result = event.results[index];
        const transcript = result[0].transcript.trim();

        if (!transcript || isEcho(transcript)) continue;

        if (result.isFinal) onFinalRef.current?.(transcript);
        else interimText += `${transcript} `;
      }

      interimText = interimText.trim();
      setInterim(interimText);

      // Caller started talking over the agent: cut playback now.
      if (speakingRef.current && interimText.split(" ").length >= BARGE_IN_MIN_WORDS) {
        onBargeInRef.current?.();
        cancelSpeech();
      }
    };

    recognizer.onerror = (event) => {
      if (event.error === "not-allowed" || event.error === "service-not-allowed") {
        wantListeningRef.current = false;
        setMicState("blocked");
      } else if (event.error !== "no-speech" && event.error !== "aborted") {
        setMicState("error");
      }
    };

    // Browsers end continuous recognition after a stretch of silence.
    recognizer.onend = () => {
      setInterim("");

      if (!wantListeningRef.current) return;

      try {
        recognizer.start();
      } catch {
        setMicState("error");
      }
    };

    recognizerRef.current = recognizer;

    try {
      recognizer.start();
    } catch {
      setMicState("error");
      return false;
    }

    return true;
  }, [cancelSpeech, isEcho]);

  const stopListening = useCallback(() => {
    wantListeningRef.current = false;
    recognizerRef.current?.stop();
    recognizerRef.current = null;
    setInterim("");
    setMicState((state) => (state === "blocked" ? state : "off"));
  }, []);

  useEffect(
    () => () => {
      wantListeningRef.current = false;
      recognizerRef.current?.abort();
      if (voiceSupport.synthesis) window.speechSynthesis.cancel();
    },
    []
  );

  return {
    micState,
    interim,
    openSpeech,
    cancelSpeech,
    startListening,
    stopListening,
  };
}
