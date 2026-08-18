/**
 * 音声読み上げ（Web Speech API）。
 *
 * 外部 API を使わず、端末の音声合成をそのまま呼ぶ。無料で、オフラインでも
 * 動く端末が多く、第一目的（英語を英語のまま）に必要な「音と意味の結び付け」を
 * 追加コストなしで賄える。未対応環境では黙って何もしない。
 */

export interface SpeakOptions {
  /** 読み上げ速度。0.5〜1.5 の範囲で使う。シャドーイング用に落とせる。 */
  readonly rate?: number;
  /** 優先する言語タグ。既定は米国英語。 */
  readonly lang?: string;
}

export function isSpeechSupported(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

/** 英語の音声を選ぶ。指定言語が無ければ任意の英語音声へ落とす。 */
function pickVoice(lang: string): SpeechSynthesisVoice | null {
  const voices = window.speechSynthesis.getVoices();
  return (
    voices.find((voice) => voice.lang === lang) ??
    voices.find((voice) => voice.lang.startsWith("en")) ??
    null
  );
}

export function speak(text: string, options: SpeakOptions = {}): void {
  if (!isSpeechSupported() || text.trim() === "") {
    return;
  }
  const lang = options.lang ?? "en-US";
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = lang;
  utterance.rate = Math.min(1.5, Math.max(0.5, options.rate ?? 1));
  const voice = pickVoice(lang);
  if (voice) {
    utterance.voice = voice;
  }
  window.speechSynthesis.speak(utterance);
}

export function stopSpeaking(): void {
  if (isSpeechSupported()) {
    window.speechSynthesis.cancel();
  }
}
