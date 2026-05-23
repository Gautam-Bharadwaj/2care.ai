/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string;
}
interface ImportMeta {
  readonly env: ImportMetaEnv;
}

// Browser Web Speech API typings are missing from default lib.dom for
// the prefixed variants — declare them so TS doesn't complain. These
// types are intentionally loose; the runtime checks live with the calls.
interface Window {
  webkitSpeechRecognition?: any;
  SpeechRecognition?: any;
}
