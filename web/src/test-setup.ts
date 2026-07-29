import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// RTL only auto-registers its cleanup when a global afterEach exists; this
// suite runs vitest without injected globals (imports over ambient magic),
// so unmounting is on us — without this every render leaks into the next
// test as "found multiple elements".
afterEach(cleanup);
