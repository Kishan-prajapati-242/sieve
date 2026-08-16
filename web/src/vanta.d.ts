// Vanta ships no types. Declared narrowly rather than `any`-ing the import,
// so the option object stays checked at the call site.
declare module "vanta/dist/vanta.net.min.js" {
  const NET: (options: Record<string, unknown>) => { destroy: () => void };
  export default NET;
}
