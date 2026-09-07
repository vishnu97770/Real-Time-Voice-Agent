const statusText = document.querySelector("#status-text");
const versionText = document.querySelector("#version-text");
const statusDot = document.querySelector("#status-dot");
const connectButton = document.querySelector("#connect-button");
const micButton = document.querySelector("#mic-button");
const sessionText = document.querySelector("#session-text");
const applicationSelect = document.querySelector("#application-select");
const createApplicationButton = document.querySelector("#create-application-button");
const refreshApplicationsButton = document.querySelector("#refresh-applications-button");
const applicationContext = document.querySelector("#application-context");
const attachButton = document.querySelector("#attach-button");
const questionInput = document.querySelector("#question-input");
const askButton = document.querySelector("#ask-button");
const transcript = document.querySelector("#transcript");
let socket;
let mediaStream;
let recorder;
let attachedApplicationId = null;

const API_BASE = "http://localhost:8000";

function addTranscript(role, text) {
  const line = document.createElement("p");
  line.className = "line " + role;
  line.textContent = (role === "agent" ? "Agent: " : "You: ") + text;
  transcript.appendChild(line);
  transcript.scrollTop = transcript.scrollHeight;
}

async function checkBackend() {
  try {
    const response = await fetch(API_BASE + "/health");
    if (!response.ok) throw new Error("HTTP " + response.status);
    const health = await response.json();
    statusText.textContent = "Connected and ready";
    versionText.textContent = health.service + " API " + health.version;
    statusDot.classList.add("online");
    await loadApplications();
  } catch (error) {
    statusText.textContent = "Backend unavailable. Start the API to connect.";
    versionText.textContent = "Voice transport is ready once a session is attached to an application.";
  }
}

async function loadApplications() {
  const response = await fetch(API_BASE + "/applications");
  if (!response.ok) throw new Error("HTTP " + response.status);
  const applications = await response.json();
  const previous = applicationSelect.value;
  applicationSelect.innerHTML = "";
  if (applications.length === 0) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "Create an application first";
    applicationSelect.appendChild(option);
  } else {
    for (const application of applications) {
      const option = document.createElement("option");
      option.value = application.application_id;
      option.textContent = application.applicant_name + " (" + application.state + ")";
      applicationSelect.appendChild(option);
    }
    if (previous && applications.some((item) => item.application_id === previous)) {
      applicationSelect.value = previous;
    }
  }
  await showContext();
}

async function showContext() {
  const applicationId = applicationSelect.value;
  if (!applicationId) {
    applicationContext.textContent = "No application selected.";
    attachedApplicationId = null;
    return;
  }
  const response = await fetch(API_BASE + "/applications/" + applicationId);
  if (!response.ok) throw new Error("HTTP " + response.status);
  const application = await response.json();
  applicationContext.textContent = JSON.stringify({
    applicant_name: application.applicant_name,
    state: application.state,
    consent: application.consent,
    documents: application.documents.length,
    metrics: application.metrics.length,
    decisions: application.decisions.length,
    risk: application.current_risk || "not yet predicted",
  }, null, 2);
}

function stopMicrophone() {
  if (recorder && recorder.state !== "inactive") recorder.stop();
  if (mediaStream) mediaStream.getTracks().forEach((track) => track.stop());
  recorder = undefined;
  mediaStream = undefined;
  micButton.textContent = "Start microphone";
}

async function startMicrophone() {
  if (!navigator.mediaDevices || !window.MediaRecorder) {
    sessionText.textContent = "This browser does not support microphone capture.";
    return;
  }
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
    recorder = new MediaRecorder(mediaStream);
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0 && socket && socket.readyState === WebSocket.OPEN) socket.send(event.data);
    };
    recorder.onstop = () => { sessionText.textContent = "Microphone stopped. Waiting for an ASR adapter."; };
    recorder.start(250);
    micButton.textContent = "Stop microphone";
    sessionText.textContent = "Microphone streaming. Waiting for an ASR adapter.";
  } catch (error) {
    sessionText.textContent = "Microphone permission was not granted.";
  }
}

function sendAttach() {
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    sessionText.textContent = "Connect the voice session before attaching.";
    return;
  }
  const applicationId = applicationSelect.value;
  if (!applicationId) {
    sessionText.textContent = "Create and select an application before attaching.";
    return;
  }
  socket.send(JSON.stringify({ type: "attach", application_id: applicationId, profile_id: "underwriting" }));
}

function sendQuestion() {
  const content = questionInput.value.trim();
  if (!content) return;
  if (attachedApplicationId) {
    addTranscript("user", content);
    questionInput.value = "";
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ type: "text", content }));
    } else {
      sessionText.textContent = "Connect and attach before asking.";
    }
  } else {
    sessionText.textContent = "Attach an application before asking.";
  }
}

checkBackend();
createApplicationButton.addEventListener("click", async () => {
  try {
    const response = await fetch(API_BASE + "/applications", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ applicant_name: "New Applicant " + Date.now() }),
    });
    if (!response.ok) throw new Error("HTTP " + response.status);
    await loadApplications();
  } catch (error) {
    sessionText.textContent = "Could not create the application.";
  }
});
refreshApplicationsButton.addEventListener("click", async () => {
  try { await loadApplications(); } catch (error) { statusText.textContent = "Application list unavailable."; }
});
applicationSelect.addEventListener("change", showContext);
attachButton.addEventListener("click", sendAttach);
askButton.addEventListener("click", sendQuestion);
questionInput.addEventListener("keydown", (event) => { if (event.key === "Enter") sendQuestion(); });

connectButton.addEventListener("click", () => {
  if (socket && socket.readyState === WebSocket.OPEN) {
    stopMicrophone();
    socket.send(JSON.stringify({ type: "end" }));
    return;
  }
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(protocol + "://" + (window.location.hostname || "localhost") + ":8000/ws/voice");
  connectButton.disabled = true; connectButton.textContent = "Connecting...";
  socket.onopen = () => {
    connectButton.disabled = false; micButton.disabled = false; attachButton.disabled = false;
    questionInput.disabled = false; askButton.disabled = false;
    connectButton.textContent = "End voice session";
  };
  socket.onmessage = (event) => {
    let message;
    try { message = JSON.parse(event.data); } catch (error) { return; }
    if (message.type === "session.ready") sessionText.textContent = "Session " + message.session_id + " is listening.";
    if (message.type === "audio.received") sessionText.textContent = "Audio transport active. " + message.bytes + " bytes received.";
    if (message.type === "session.attached") {
      attachedApplicationId = message.application_id;
      sessionText.textContent = "Attached to application " + message.application_id + " (" + message.profile_id + ").";
    }
    if (message.type === "agent.response") addTranscript("agent", message.text || "");
    if (message.type === "agent.error") addTranscript("agent", "Error: " + (message.detail || "agent unavailable"));
    if (message.type === "error") sessionText.textContent = "Error: " + (message.detail || message.code || "unknown");
    if (message.type === "session.ended") { stopMicrophone(); sessionText.textContent = "Session ended."; }
  };
  socket.onclose = () => {
    stopMicrophone(); micButton.disabled = true; attachButton.disabled = true; askButton.disabled = true; questionInput.disabled = true;
    attachedApplicationId = null;
    connectButton.disabled = false; connectButton.textContent = "Connect voice session";
  };
  socket.onerror = () => { sessionText.textContent = "Voice transport unavailable."; };
});
micButton.addEventListener("click", () => {
  if (recorder && recorder.state !== "inactive") stopMicrophone();
  else startMicrophone();
});