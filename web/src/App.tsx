// The shell: nav, routes, route transition.
//
// The landing page is the root now and the search moved to /search, because
// an unauthenticated visitor arriving at a bare search box has no idea what
// this is. Collections sit behind RequireAuth since they belong to a user.
//
// Route transition is a crossfade plus a small y-offset with mode="wait" —
// with only a crossfade, overlapping the outgoing and incoming routes muddies
// both.
import { AnimatePresence, motion, useReducedMotion, useScroll, useTransform } from "motion/react";
import { BrowserRouter, Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { AuthPage, RequireAuth } from "./AuthPage";
import { Background } from "./Background";
import { LiveBackground } from "./LiveBackground";
import { VerifyBanner } from "./VerifyBanner";
import { useAuth } from "./auth";
import { CollectionPage } from "./CollectionPage";
import { CollectionsPage } from "./CollectionsPage";
import { InvitePage } from "./InvitePage";
import { Footer, LandingPage } from "./LandingPage";
import { routeVariants } from "./motion";
import { SearchPage } from "./SearchPage";
import { Button, ButtonLink, Container, ThemeToggle } from "./ui";

function Wordmark() {
  return (
    <Link to="/" className="group flex items-center gap-2.5">
      {/* The mark is the mechanism: two bars, one per arm, fusing. */}
      <span className="flex h-5 w-5 flex-col justify-center gap-[3px]" aria-hidden="true">
        <span className="h-[3px] w-full rounded-full bg-keyword-400 transition-all duration-300 group-hover:w-3/4" />
        <span className="h-[3px] w-3/4 rounded-full bg-fusion transition-all duration-300 group-hover:w-full" />
        <span className="h-[3px] w-1/2 rounded-full bg-semantic-400 transition-all duration-300 group-hover:w-3/4" />
      </span>
      <span className="text-[15px] font-semibold tracking-tight text-ink-50">Sieve</span>
    </Link>
  );
}

function navClass({ isActive }: { isActive: boolean }) {
  return `relative text-sm transition-colors ${
    isActive ? "text-ink-50" : "text-ink-400 hover:text-ink-100"
  }`;
}

function Nav() {
  const { user, logout } = useAuth();
  const { scrollY } = useScroll();
  const reduce = useReducedMotion();
  // The nav gains its border and blur only once the page has scrolled, so the
  // hero meets the top of the window without a line across it.
  const border = useTransform(scrollY, [0, 60], ["rgba(255,255,255,0)", "rgba(255,255,255,0.09)"]);
  const bg = useTransform(scrollY, [0, 60], ["rgba(8,8,10,0)", "rgba(8,8,10,0.72)"]);

  return (
    <motion.header
      style={reduce ? undefined : { borderBottomColor: border, backgroundColor: bg }}
      className="sticky top-0 z-50 border-b border-transparent backdrop-blur-xl"
    >
      <Container className="flex h-14 items-center justify-between gap-3 sm:gap-6">
        <div className="flex items-center gap-4 sm:gap-7">
          <Wordmark />
          <nav className="hidden items-center gap-4 sm:flex sm:gap-5">
            <NavLink to="/search" className={navClass}>
              Search
            </NavLink>
            <NavLink to="/collections" className={navClass}>
              Collections
            </NavLink>
          </nav>
        </div>

        <div className="flex items-center gap-2 sm:gap-3">
          <ThemeToggle />
          {user ? (
            <>
              <span className="hidden max-w-[180px] truncate font-mono text-[11px] text-ink-500 lg:inline">
                {user.email}
              </span>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => void logout()}
                className="whitespace-nowrap"
              >
                Sign out
              </Button>
            </>
          ) : (
            <>
              {/* 390px cannot hold wordmark + two links + theme + two CTAs.
                  The secondary action is the one to drop: /signup carries a
                  link to /login, so nothing becomes unreachable. */}
              <ButtonLink to="/login" variant="ghost" size="sm" className="hidden sm:inline-flex">
                Sign in
              </ButtonLink>
              <ButtonLink to="/signup" size="sm" className="whitespace-nowrap">
                <span className="sm:hidden">Sign in</span>
                <span className="hidden sm:inline">Get started</span>
              </ButtonLink>
            </>
          )}
        </div>
      </Container>
    </motion.header>
  );
}

function Shell() {
  const location = useLocation();
  const isLanding = location.pathname === "/";
  return (
    <div className="relative flex min-h-screen flex-col">
      {/* Two layers: the CSS aurora is the colour field, the canvas
          network is the structure that moves over it. */}
      <Background />
      <LiveBackground />
      <Nav />
      <VerifyBanner />
      <main className="flex-1">
        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={location.pathname}
            variants={routeVariants}
            initial="initial"
            animate="animate"
            exit="exit"
          >
            <Routes location={location}>
              <Route path="/" element={<LandingPage />} />
              <Route
                path="/search"
                element={
                  <Container className="py-10">
                    <SearchPage />
                  </Container>
                }
              />
              <Route path="/login" element={<AuthPage mode="login" />} />
              <Route path="/signup" element={<AuthPage mode="signup" />} />
              <Route
                path="/collections"
                element={
                  <RequireAuth>
                    <Container className="py-10">
                      <CollectionsPage />
                    </Container>
                  </RequireAuth>
                }
              />
              <Route
                path="/invite/:token"
                element={
                  <RequireAuth>
                    <InvitePage />
                  </RequireAuth>
                }
              />
              <Route
                path="/collections/:id"
                element={
                  <RequireAuth>
                    <Container className="py-10">
                      <CollectionPage />
                    </Container>
                  </RequireAuth>
                }
              />
            </Routes>
          </motion.div>
        </AnimatePresence>
      </main>
      {isLanding && <Footer />}
    </div>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <Shell />
    </BrowserRouter>
  );
}
