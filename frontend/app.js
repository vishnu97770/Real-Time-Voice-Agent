const statusText = document.querySelector("#status-text");
const versionText = document.querySelector("#version-text");
const statusDot = document.querySelector("#status-dot");
const connectButton = document.querySelector("#connect-button");
const micButton = document.querySelector("#mic-button");
const sessionText = document.querySelector("#session-text");
let socket;
let mediaStream;
let recorder;

async function checkBackend() {
  try {
    const response = await fetch("http://localhost:8000/health");
    if (!response.ok) throw new Error("HTTP " + response.status);
    const health = await response.json();
    statusText.textContent = "Connected and ready";
    versionText.textContent = health.service + " API " + health.version;
    statusDot.classList.add("online");
  } catch (error) {
    statusText.textContent = "Backend unavailable. Start the API to connect.";
    versionText.textContent = "No voice provider is configured in this foundation slice.";
  }
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
    recorder.onstop = () => { sessionText.textContent = "Microphone stopped. Audio was sent as transport frames only."; };
    recorder.start(250);
    micButton.textContent = "Stop microphone";
    sessionText.textContent = "Microphone streaming. Waiting for an ASR adapter.";
  } catch (error) {
    sessionText.textContent = "Microphone permission was not granted.";
  }
}

checkBackend();
connectButton.addEventListener("click", () => {
  if (socket && socket.readyState === WebSocket.OPEN) {
    stopMicrophone();
    socket.send(JSON.stringify({ type: "end" }));
    return;
  }
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(protocol + "://" + (window.location.hostname || "localhost") + ":8000/ws/voice");
  connectButton.disabled = true; connectButton.textContent = "Connecting...";
  socket.onopen = () => { connectButton.disabled = false; micButton.disabled = false; connectButton.textContent = "End voice session"; };
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "session.ready") sessionText.textContent = "Session " + message.session_id + " is listening.";
    if (message.type === "audio.received") sessionText.textContent = "Audio transport active. " + message.bytes + " bytes received.";
    if (message.type === "session.ended") { stopMicrophone(); sessionText.textContent = "Session ended."; }
  };
  socket.onclose = () => { stopMicrophone(); micButton.disabled = true; connectButton.disabled = false; connectButton.textContent = "Connect voice session"; };
  socket.onerror = () => { sessionText.textContent = "Voice transport unavailable."; };
});
micButton.addEventListener("click", () => {
  if (recorder && recorder.state !== "inactive") stopMicrophone();
  else startMicrophone();
});
