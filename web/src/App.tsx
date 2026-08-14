// The shell: nav, routes, and the route transition. Three routes — search,
// collections, one collection — so transitions are the connective tissue.
//
// Crossfade plus a small y-offset (Kishan, 2026-08-13). Card -> detail expand
// is deferred on cost, not dismissed: it is the only genuinely spatial
// transition in the app and is scheduled for after View B
// (docs/plans/ui-assembly-plan.md).
import { AnimatePresence, motion } from "motion/react";
import {
  BrowserRouter,
  Link,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { CollectionPage } from "./CollectionPage";
import { CollectionsPage } from "./CollectionsPage";
import { routeVariants } from "./motion";
import { SearchPage } from "./SearchPage";

function Shell() {
  const location = useLocation();
  return (
    <main className="mx-auto max-w-4xl p-6">
      <header className="mb-6 flex items-baseline gap-5">
        <Link to="/" className="text-xl font-bold text-slate-900">
          Sieve
        </Link>
        <nav className="flex gap-4 text-sm">
          <Link to="/" className="text-slate-600 hover:text-slate-900">
            Search
          </Link>
          <Link to="/collections" className="text-slate-600 hover:text-slate-900">
            Collections
          </Link>
        </nav>
      </header>

      {/* mode="wait" so the outgoing route clears before the incoming one
          lands — with only a crossfade, overlapping them muddies both. */}
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={location.pathname}
          variants={routeVariants}
          initial="initial"
          animate="animate"
          exit="exit"
        >
          <Routes location={location}>
            <Route path="/" element={<SearchPage />} />
            <Route path="/collections" element={<CollectionsPage />} />
            <Route path="/collections/:id" element={<CollectionPage />} />
          </Routes>
        </motion.div>
      </AnimatePresence>
    </main>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <Shell />
    </BrowserRouter>
  );
}
