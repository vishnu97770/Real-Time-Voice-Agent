// Connects a call transport (server brain or local rules) to the browser voice
// loop and to React state. Turns are serialized: one at a time, in the order the
// caller spoke, so a reply is never spoken over another. A reply is spoken while
// it is still arriving, sentence by sentence.

import { useCallback, useEffect, useRef, useState } from "react";
import { redactSensitive } from "./guardrails.js";
import { formatTime } from "./format.js";
import { createLocalTransport, createRemoteTransport, detectBackend } from "./transports.js";
import { useVoice } from "./useVoice.js";

const CONNECTION_PROBLEM =
  "I lost the connection to the server. Please try again in a moment.";

// `agentConfig` is the resolved agent configuration (see agentConfig.js), or null.
// Today only its voice takes effect here. The rest is the context a call will
// send to the server brain once the API accepts it (createRemoteTransport).
export function useVoiceAgent(profile, { attach = null, customerRef = null, agentConfig = null } = {}) {
  const [callState, setCallState] = useState("idle"); // idle | connecting | listening | processing | speaking | ended
  const [messages, setMessages] = useState([]);
  const [pending, setPending] = useState(null);
  const [duration, setDuration] = useState(0);
  const [summary, setSummary] = useState(null);
  const [callError, setCallError] = useState(null);
  const [callConfig, setCallConfig] = useState(null); // the agent configuration this call started with
  const [startedAt, setStartedAt] = useState(null); // when the call began, in ms
  const [summaryPending, setSummaryPending] = useState(false); // the call ended, its result is still coming
  // Which brain answers calls: checking | server | local
  const [brain, setBrain] = useState({ source: "checking", name: null, note: null });

  const transportRef = useRef(null);
  const startedRef = useRef(0);
  const activeRef = useRef(false);
  const callGenerationRef = useRef(0);
  const queueRef = useRef(Promise.resolve());
  const turnAbortRef = useRef(null);
  const messagesRef = useRef([]);
  const nextIdRef = useRef(1);
  const sendRef = useRef(null);
  const interruptRef = useRef(null);
  const endCallRef = useRef(null);

  const voice = useVoice({
    // Speech that arrives after the call has ended must not start a new call.
    onFinal: (text) => {
      if (activeRef.current) sendRef.current?.(text);
    },
    onBargeIn: () => interruptRef.current?.(),
    voiceName: agentConfig?.voice ?? "",
  });
  const { openSpeech, cancelSpeech, startListening, stopListening } = voice;

  const elapsed = () => Math.floor((Date.now() - startedRef.current) / 1000);

  const isActive = callState !== "idle" && callState !== "ended";

  useEffect(() => {
    if (!isActive) return undefined;

    const timer = setInterval(() => setDuration(elapsed()), 500);

    return () => clearInterval(timer);
  }, [isActive]);

  useEffect(() => {
    let cancelled = false;

    detectBackend().then(({ available, brain: name, telephony }) => {
      if (!cancelled) {
        setBrain(
          available
            ? { source: "server", name, note: null, telephony }
            : { source: "local", name: "local-rules", note: null, telephony: false }
        );
      }
    });

    return () => {
      cancelled = true;
    };
  }, []);

  const commit = useCallback((next) => {
    messagesRef.current = next;
    setMessages(next);
  }, []);

  const addMessage = useCallback(
    (message) => {
      const entry = { id: nextIdRef.current++, time: formatTime(elapsed()), ...message };

      commit([...messagesRef.current, entry]);

      return entry.id;
    },
    [commit]
  );

  const patchMessage = useCallback(
    (id, patch) => {
      commit(
        messagesRef.current.map((message) =>
          message.id === id
            ? { ...message, ...(typeof patch === "function" ? patch(message) : patch) }
            : message
        )
      );
    },
    [commit]
  );

  // Stops the agent mid-reply: playback and the reply still being generated.
  const interruptAgent = useCallback(() => {
    turnAbortRef.current?.abort();
    cancelSpeech();
  }, [cancelSpeech]);

  useEffect(() => {
    interruptRef.current = interruptAgent;
  }, [interruptAgent]);

  const speakWhole = useCallback(
    async (id, text) => {
      setCallState("speaking");

      const speech = openSpeech();

      speech.push(text);
      speech.end();

      if (!(await speech.done) && activeRef.current) {
        patchMessage(id, { interrupted: true });
        transportRef.current?.interrupted();
      }

      if (activeRef.current) setCallState("listening");
    },
    [openSpeech, patchMessage]
  );

  const runTurn = useCallback(
    async (text) => {
      const transport = transportRef.current;

      if (!activeRef.current || !transport) return;

      addMessage({ speaker: "You", text: redactSensitive(text) });
      setCallState("processing");

      const controller = new AbortController();
      const speech = openSpeech();
      let agentId = null;
      let agentHangsUp = false;

      turnAbortRef.current = controller;

      const ensureMessage = () => {
        agentId ??= addMessage({ speaker: "Agent", text: "", toolCalls: [] });

        return agentId;
      };

      try {
        for await (const event of transport.turn(text, controller.signal)) {
          if (!activeRef.current || controller.signal.aborted) break;

          if (event.type === "tool_call") {
            const call = {
              name: event.name,
              args: event.args,
              result: event.result,
              guarded: event.guarded,
            };

            patchMessage(ensureMessage(), (message) => ({
              toolCalls: [...message.toolCalls, call],
            }));
          } else if (event.type === "sentence") {
            patchMessage(ensureMessage(), (message) => ({
              text: message.text ? `${message.text} ${event.text}` : event.text,
            }));
            speech.push(event.text);
            setCallState("speaking");
          } else if (event.type === "done") {
            agentHangsUp = Boolean(event.ended);
            setPending(event.pending);
            if (event.blocked) patchMessage(ensureMessage(), { blocked: true });
          }
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          console.error(error);
          patchMessage(ensureMessage(), (message) => ({
            text: message.text ? `${message.text} ${CONNECTION_PROBLEM}` : CONNECTION_PROBLEM,
          }));
          speech.push(CONNECTION_PROBLEM);
        }
      } finally {
        // Also closes the stream when we stopped reading early.
        controller.abort();
      }

      speech.end();

      const completed = await speech.done;

      if (!activeRef.current) return;

      if (!completed) {
        if (agentId) patchMessage(agentId, { interrupted: true });
        transport.interrupted();
      }

      // The agent said goodbye and ended the call (wrong person, time limit).
      if (agentHangsUp) {
        endCallRef.current?.();
        return;
      }

      setCallState("listening");
    },
    [addMessage, openSpeech, patchMessage]
  );

  const enqueue = useCallback((job) => {
    queueRef.current = queueRef.current.then(job).catch((error) => console.error(error));
  }, []);

  const chooseTransport = useCallback(async () => {
    // An outbound call exists only on the server: there is nothing to fall back to.
    if (attach) {
      const remote = createRemoteTransport(profile, "server", attach);

      return { transport: remote, greeting: await remote.open() };
    }

    const { available, brain: name } = await detectBackend();

    if (available) {
      const remote = createRemoteTransport(profile, name, null, customerRef);

      try {
        const greeting = await remote.open();

        setBrain({ source: "server", name, note: null });

        return { transport: remote, greeting };
      } catch (error) {
        console.error(error);
      }
    }

    const local = createLocalTransport(profile);

    setBrain({
      source: "local",
      name: local.name,
      note: available ? "Server call failed, using the local runtime." : null,
    });

    return { transport: local, greeting: await local.open() };
  }, [profile, attach, customerRef]);

  const beginCall = useCallback(
    async (firstText) => {
      const generation = ++callGenerationRef.current;

      activeRef.current = true;
      startedRef.current = Date.now();
      queueRef.current = Promise.resolve();
      nextIdRef.current = 1;

      commit([]);
      setPending(null);
      setSummary(null);
      setCallError(null);
      setDuration(0);
      setStartedAt(startedRef.current);
      setCallConfig(agentConfig);
      setCallState("connecting");

      startListening();

      // Connecting happens inside the turn queue, so anything the caller says
      // meanwhile waits behind the greeting instead of being dropped.
      enqueue(async () => {
        let connected;

        try {
          connected = await chooseTransport();
        } catch (error) {
          console.error(error);
          activeRef.current = false;
          stopListening();
          setCallError("The call could not be connected. It may have expired or been answered already.");
          setCallState("ended");
          return;
        }

        const { transport, greeting } = connected;

        // The caller may have ended the call or switched profile while connecting.
        if (!activeRef.current || callGenerationRef.current !== generation) {
          transport.close(0, []).catch(() => {});
          return;
        }

        transportRef.current = transport;
        startedRef.current = Date.now();
        setStartedAt(startedRef.current);

        // The call always opens with the AI disclosure, before anything else.
        const greetingId = addMessage({ speaker: "Agent", text: greeting });

        await speakWhole(greetingId, greeting);
        if (firstText) await runTurn(firstText);
      });
    },
    [addMessage, agentConfig, chooseTransport, commit, enqueue, runTurn, speakWhole, startListening, stopListening]
  );

  // Typed text, suggestion chips and recognized speech all take this one path.
  const sendText = useCallback(
    (raw) => {
      const text = raw.trim();

      if (!text) return;

      if (!activeRef.current) {
        beginCall(text);
        return;
      }

      // A new caller turn always cuts whatever the agent is saying.
      interruptAgent();
      enqueue(() => runTurn(text));
    },
    [beginCall, enqueue, interruptAgent, runTurn]
  );

  useEffect(() => {
    sendRef.current = sendText;
  }, [sendText]);

  const endCall = useCallback(async () => {
    if (!activeRef.current) return;

    activeRef.current = false;
    interruptAgent();
    stopListening();

    const seconds = elapsed();

    setDuration(seconds);
    setPending(null);
    setCallState("ended");

    const transport = transportRef.current;

    if (!transport) return;

    setSummaryPending(true);

    try {
      setSummary(await transport.close(seconds, messagesRef.current));
    } catch (error) {
      console.error(error);
    } finally {
      setSummaryPending(false);
    }
  }, [interruptAgent, stopListening]);

  useEffect(() => {
    endCallRef.current = endCall;
  }, [endCall]);

  const reset = useCallback(() => {
    activeRef.current = false;
    callGenerationRef.current += 1;
    interruptAgent();
    stopListening();

    transportRef.current = null;
    commit([]);
    setPending(null);
    setSummary(null);
    setCallError(null);
    setSummaryPending(false);
    setStartedAt(null);
    setCallConfig(null);
    setDuration(0);
    setCallState("idle");
  }, [commit, interruptAgent, stopListening]);

  const clearMessages = useCallback(() => commit([]), [commit]);

  return {
    callState,
    duration,
    messages,
    pending,
    summary,
    summaryPending,
    startedAt,
    callConfig,
    callError,
    brain,
    voice,
    beginCall: () => beginCall(),
    sendText,
    endCall,
    reset,
    clearMessages,
  };
}
