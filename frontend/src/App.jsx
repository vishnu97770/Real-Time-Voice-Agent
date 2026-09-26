import { lazy, Suspense } from "react";
import AppGate from "./auth/AppGate.jsx";
import AuthProvider from "./auth/AuthProvider.jsx";
import NotFound from "./components/NotFound.jsx";
import Splash from "./components/Splash.jsx";
import { useRouter } from "./router/context.js";
import RouterProvider from "./router/RouterProvider.jsx";
import { isAppRoute } from "./router/router.js";

const Landing = lazy(() => import("./site/Landing.jsx"));
const SignIn = lazy(() => import("./site/SignIn.jsx"));
const OperatorConsole = lazy(() => import("./app/OperatorConsole.jsx"));
const CalleeRoute = lazy(() => import("./app/CalleeRoute.jsx"));

// Every URL lands on something: a screen, the sign-in redirect, the retry screen, or "not found".
// Nothing falls through to a loading mark.
function Screen() {
  const { route, search } = useRouter();

  // A link like /?job=JOB-...&token=... is a phone call for someone: the incoming-call screen,
  // whatever else the app is doing.
  const jobId = search.get("job");
  const token = search.get("token");

  if (jobId && token) return <CalleeRoute jobId={jobId} token={token} />;

  if (route === "landing") return <Landing />;
  if (route === "signin") return <SignIn />;

  if (isAppRoute(route)) {
    return (
      <AppGate>
        <OperatorConsole />
      </AppGate>
    );
  }

  return <NotFound />;
}

export default function App() {
  return (
    <RouterProvider>
      <AuthProvider>
        <Suspense fallback={<Splash />}>
          <Screen />
        </Suspense>
      </AuthProvider>
    </RouterProvider>
  );
}
