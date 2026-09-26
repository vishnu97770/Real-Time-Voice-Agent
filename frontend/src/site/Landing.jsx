import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import Link from "../router/Link.jsx";
import Chapters from "./Chapters.jsx";
import Logo from "../components/Logo.jsx";
import Nav from "./Nav.jsx";
import Hero from "./sections/Hero.jsx";
import { pickTier, readDevice } from "./hooks/tier.js";
import "./landing.css";

import Defer from "./Defer.jsx";

const VoiceStage = lazy(() => import("./stage/VoiceStage.jsx"));
const Explain = lazy(() => import("./sections/Explain.jsx"));
const Demo = lazy(() => import("./sections/Demo.jsx"));
const UseCases = lazy(() => import("./sections/UseCases.jsx"));
const HowItWorks = lazy(() => import("./sections/HowItWorks.jsx"));
const ConfigPreview = lazy(() => import("./sections/ConfigPreview.jsx"));
const Architecture = lazy(() => import("./sections/Architecture.jsx"));
const RealtimeMatters = lazy(() => import("./sections/RealtimeMatters.jsx"));
const CallToAction = lazy(() => import("./sections/CallToAction.jsx"));

const idle = (callback) => (window.requestIdleCallback ? window.requestIdleCallback(callback, { timeout: 1200 }) : setTimeout(callback, 250));
const cancelIdle = (handle) => (window.cancelIdleCallback ? window.cancelIdleCallback(handle) : clearTimeout(handle));

// The public site. It renders at once (text, CSS orb, buttons); the 3D scene and every section
// below the hero are fetched afterwards, so the first paint never waits for them.
export default function Landing() {
  const [tier] = useState(() => pickTier(readDevice()));
  const [stageStatus, setStageStatus] = useState(tier === "off" ? "off" : "loading"); // loading | ready | off
  const [stageMounted, setStageMounted] = useState(false);

  useEffect(() => {
    if (tier === "off") return undefined;

    const handle = idle(() => setStageMounted(true));

    return () => cancelIdle(handle);
  }, [tier]);

  const onReady = useCallback(() => setStageStatus("ready"), []);
  const onGiveUp = useCallback(() => setStageStatus("off"), []);

  return (
    <div className="lp" data-stage={stageStatus}>
      <Nav />
      <Chapters />

      {stageMounted && stageStatus !== "off" && <VoiceStageBoundary tier={tier} onReady={onReady} onGiveUp={onGiveUp} />}

      <main>
        <Hero />
        <Defer id="what" chapter="What it is" minHeight="430vh">
          <Explain />
        </Defer>
        <Defer id="demo" chapter="Live demo" minHeight="820px">
          <Demo />
        </Defer>
        <Defer id="use-cases" chapter="What it can do" minHeight="330vh">
          <UseCases />
        </Defer>
        <Defer id="how" chapter="How it works" minHeight="640vh">
          <HowItWorks />
        </Defer>
        <Defer id="build" chapter="Build an agent" minHeight="860px">
          <ConfigPreview />
        </Defer>
        <Defer id="architecture" chapter="The real-time loop" minHeight="900px">
          <Architecture />
        </Defer>
        <Defer id="realtime" chapter="Why real-time" minHeight="1000px">
          <RealtimeMatters />
        </Defer>
        <Defer id="get-started" chapter="Get started" minHeight="100vh">
          <CallToAction />
        </Defer>
      </main>

      <footer className="lp-footer">
        <div className="lp-wrap">
          <Link to="/" className="lp-brand">
            <Logo />
            <span>
              <b>Real-Time</b> Voice Agent
            </span>
          </Link>
          <nav aria-label="Footer">
            <Link to="/signin" transition>Sign in</Link>
          </nav>
        </div>
      </footer>
    </div>
  );
}


function VoiceStageBoundary(props) {
  return (
    <Suspense fallback={null}>
      <VoiceStage {...props} />
    </Suspense>
  );
}
