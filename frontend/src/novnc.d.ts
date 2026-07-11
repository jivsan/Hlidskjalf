declare module "@novnc/novnc" {
  interface RFBOptions {
    shared?: boolean;
    credentials?: { username?: string; password?: string; target?: string };
    repeaterID?: string;
    wsProtocols?: string[];
  }

  interface RFBEventMap {
    connect: CustomEvent<Record<string, never>>;
    disconnect: CustomEvent<{ clean: boolean }>;
    credentialsrequired: CustomEvent<{ types: string[] }>;
    securityfailure: CustomEvent<{ status: number; reason?: string }>;
    desktopname: CustomEvent<{ name: string }>;
    clipboard: CustomEvent<{ text: string }>;
  }

  export default class RFB extends EventTarget {
    constructor(target: HTMLElement, url: string, options?: RFBOptions);

    viewOnly: boolean;
    scaleViewport: boolean;
    resizeSession: boolean;
    clipViewport: boolean;
    background: string;
    qualityLevel: number;
    compressionLevel: number;

    disconnect(): void;
    sendCredentials(credentials: { username?: string; password?: string; target?: string }): void;
    sendCtrlAltDel(): void;
    sendKey(keysym: number, code: string | null, down?: boolean): void;
    focus(): void;
    blur(): void;
    clipboardPasteFrom(text: string): void;

    addEventListener<K extends keyof RFBEventMap>(
      type: K,
      listener: (ev: RFBEventMap[K]) => void,
      options?: boolean | AddEventListenerOptions,
    ): void;
    removeEventListener<K extends keyof RFBEventMap>(
      type: K,
      listener: (ev: RFBEventMap[K]) => void,
      options?: boolean | EventListenerOptions,
    ): void;
  }
}
