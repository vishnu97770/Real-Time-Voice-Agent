import { useEffect, useRef, useState } from "react";
import TopNavbar from "./components/voice-agent/TopNavbar";
import Sidebar from "./components/voice-agent/Sidebar";
import VoicePanel from "./components/voice-agent/VoicePanel";
import AgentResponse from "./components/voice-agent/AgentResponse";
import CallSummary from "./components/voice-agent/CallSummary";
import "./styles/voice-agent.css";

const mockResponses = [
  {
    speaker: "You",
    text: "What is the status of application APP-1024?",
    time: "00:00",
  },
  {
    speaker: "Agent",
    text: "Let me check that for you. Application APP-1024 is currently under financial review. All required documents have been received except the latest bank statement. The current risk score is 0.28, which indicates moderate risk.",
    time: "00:02",
  },
];

function App() {
  const [callState, setCallState] = useState("idle");
  const [duration, setDuration] = useState(0);
  const [messages, setMessages] = useState(mockResponses);
  const timerRef = useRef(null);

  useEffect(() => {
    if (callState === "listening" || callState === "speaking") {
      timerRef.current = setInterval(() => {
        setDuration((previous) => previous + 1);
      }, 1000);
    }

    return () => clearInterval(timerRef.current);
  }, [callState]);

  const startConversation = () => {
    setCallState("listening");
    setDuration(0);

    setMessages([
      {
        speaker: "You",
        text: "What is the status of application APP-1024?",
        time: "00:00",
      },
      {
        speaker: "Agent",
        text: "Let me check that for you...",
        time: "00:02",
      },
    ]);
  };

  const stopConversation = () => {
    clearInterval(timerRef.current);
    setCallState("ended");
  };

  const handlePrompt = (prompt) => {
    if (callState === "idle" || callState === "ended") {
      setCallState("listening");
      setDuration(0);
    }

    const newMessage = {
      speaker: "You",
      text: prompt,
      time: formatTime(duration),
    };

    setMessages((previous) => [...previous, newMessage]);

    setTimeout(() => {
      setCallState("processing");

      setTimeout(() => {
        setMessages((previous) => [
          ...previous,
          {
            speaker: "Agent",
            text: getMockResponse(prompt),
            time: formatTime(duration + 2),
          },
        ]);

        setCallState("speaking");

        setTimeout(() => {
          setCallState("listening");
        }, 2500);
      }, 700);
    }, 500);
  };

  const resetCall = () => {
    clearInterval(timerRef.current);
    setCallState("idle");
    setDuration(0);
    setMessages([]);
  };

  return (
    <div className="voice-app">
      <TopNavbar />

      <div className="voice-layout">
        <Sidebar callState={callState} />

        <main className="voice-main">
          <VoicePanel
            callState={callState}
            duration={duration}
            onStart={startConversation}
            onStop={stopConversation}
            onPrompt={handlePrompt}
            onReset={resetCall}
          />

          <div className="voice-right-column">
            <AgentResponse
              messages={messages}
              onClear={() => setMessages([])}
            />

            {callState === "ended" ? (
              <CallSummary duration={duration} />
            ) : (
              <div className="summary-placeholder">
                <div className="placeholder-icon">▤</div>

                <h3>Call Summary</h3>

                <p>
                  The call summary will automatically appear here after the
                  conversation ends.
                </p>
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}

function formatTime(seconds) {
  const minutes = Math.floor(seconds / 60);
  const remaining = seconds % 60;

  return `${String(minutes).padStart(2, "0")}:${String(
    remaining
  ).padStart(2, "0")}`;
}

function getMockResponse(prompt) {
  const value = prompt.toLowerCase();

  if (value.includes("pending")) {
    return "There are currently 3 applications pending review. Two are waiting for document verification and one requires additional financial analysis.";
  }

  if (value.includes("risk")) {
    return "The current application has a moderate risk profile. The main risk factors are the debt-to-income ratio, recent revenue variation, and the pending bank statement.";
  }

  if (value.includes("summarize")) {
    return "The applicant has submitted the required financial documents and is currently under review. The main outstanding item is the latest bank statement.";
  }

  if (value.includes("call")) {
    return "I can initiate an outbound call to the applicant or schedule a follow-up call based on your preferred time.";
  }

  if (value.includes("schedule")) {
    return "A follow-up call can be scheduled. The call orchestration service will trigger the outbound telephony workflow at the selected time.";
  }

  return "I understand. I can retrieve application information, analyze financial data, explain risk predictions, retrieve evidence, and perform supported underwriting actions.";
}

export default App;