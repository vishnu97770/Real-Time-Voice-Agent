import { useEffect, useMemo, useState } from "react";
import AgentResponse from "./AgentResponse";
import CallVisualizer from "../voice/CallVisualizer";
import { declineJob, fetchRing } from "../../runtime/transports.js";
import { formatTime } from "../../runtime/format.js";
import { VOICE_STATE_LABEL, visualForCallState } from "../../runtime/voiceState.js";
import { voiceSupport } from "../../runtime/useVoice.js";
import { useVoiceAgent } from "../../runtime/useVoiceAgent.js";

// What the person being called sees. They open the link they were sent, the
// "phone" rings, and they answer or decline. This is the seam a telephony
// provider will later replace: the server side of the call does not change.
export default function CalleeApp({ jobId, token }) {
  const [ring, setRing] = useState({ state: "loading" }); // loading | ringing | unavailable | declined
  const [draft, setDraft] = useState("");

  const profile = useMemo(() => ({ id: ring.profile_id }), [ring.profile_id]);
  const attach = useMemo(() => ({ jobId, token }), [jobId, token]);
  const agent = useVoiceAgent(profile, { attach });

  useEffect(() => {
    document.documentElement.setAttribute(
      "data-theme",
      localStorage.getItem("voice-agent-theme") || "dark"
    );
  }, []);

  useEffect(() => {
    let cancelled = false;

    fetchRing(jobId, token)
      .then((info) => {
        if (!cancelled) {
          setRing(
            info.status === "ringing"
              ? { state: "ringing", ...info }
              : { state: "unavailable", ...info }
          );
        }
      })
      .catch(() => {
        if (!cancelled) setRing({ state: "unavailable" });
      });

    return () => {
      cancelled = true;
    };
  }, [jobId, token]);

  const decline = async () => {
    try {
      await declineJob(jobId, token);
    } catch {
      // Already gone: nothing more to decline.
    }

    setRing((current) => ({ ...current, state: "declined" }));
  };

  const inCall = agent.callState !== "idle" && agent.callState !== "ended";
  const stateText = {
    connecting: "Connecting...",
    listening: "Listening...",
    speaking: "Speaking...",
    processing: "Thinking...",
  };

  const submit = (event) => {
    event.preventDefault();
    agent.sendText(draft);
    setDraft("");
  };

  let body;

  if (ring.state === "loading") {
    body = <div className="state-block is-loading">Checking this call...</div>;
  } else if (ring.state === "declined") {
    body = <div className="state-block">You declined the call. You can close this page.</div>;
  } else if (agent.callState === "ended") {
    body = (
      <div className="state-block">
        {agent.callError ?? "The call has ended. Thank you. You can close this page."}
      </div>
    );
  } else if (inCall) {
    body = (
      <>
        <div className="callee-orb">
          <CallVisualizer
            state={visualForCallState(agent.callState, Boolean(agent.callError))}
            orbIcon="microphone"
            statusLabel={stateText[agent.callState] ?? VOICE_STATE_LABEL[agent.callState]}
            size="lg"
          />
        </div>

        <div className="timer callee-timer">{formatTime(agent.duration)}</div>

        <div className="live-caption" aria-live="polite">
          {agent.voice.interim ? `“${agent.voice.interim}”` : " "}
        </div>

        {agent.voice.micState === "blocked" && (
          <p className="voice-notice">Microphone access is blocked. You can type your replies below.</p>
        )}
        {!voiceSupport.recognition && (
          <p className="voice-notice">This browser can't hear you (use Chrome or Edge). You can type below.</p>
        )}

        <div className="callee-transcript">
          <AgentResponse
            title="Conversation"
            messages={agent.messages}
            pending={agent.pending}
            onRespond={agent.sendText}
            showTools={false}
          />
        </div>

        <form className="type-row" onSubmit={submit}>
          <input
            type="text"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="Or type your reply..."
            aria-label="Type a message"
          />
          <button type="submit" disabled={!draft.trim()}>
            Send
          </button>
        </form>

        <button className="stop-button" onClick={agent.endCall}>
          <span className="stop-square" />
          End call
        </button>
      </>
    );
  } else if (ring.state === "ringing") {
    body = (
      <>
        <div className="callee-orb">
          <CallVisualizer
            state="connecting"
            orbIcon="phone"
            statusLabel="Incoming call"
            size="lg"
            showWaveform={false}
          />
        </div>

        <p className="callee-note">
          <strong>{ring.organisation}</strong> is calling. It is an AI assistant, and this call is
          recorded as a transcript for the business.
        </p>

        <div className="callee-buttons">
          <button className="callee-answer" onClick={agent.beginCall}>
            Answer
          </button>
          <button className="callee-decline" onClick={decline}>
            Decline
          </button>
        </div>
      </>
    );
  } else {
    body = (
      <div className="state-block">
        This call is no longer available. It may have been answered, declined or timed out.
      </div>
    );
  }

  return (
    <div className="callee-page">
      <header className="callee-header">
        <div className="brand-logo">
          <span>AI</span>
        </div>
        <h1>Real-Time Voice Agent</h1>
      </header>

      <main className="callee-card">{body}</main>
    </div>
  );
}
