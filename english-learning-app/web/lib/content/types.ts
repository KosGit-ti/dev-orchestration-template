/**
 * 学習コンテンツの型定義。
 *
 * 第一目的（英語を英語のまま理解する）に従い、いずれの型にも日本語訳の
 * フィールドを設けない。意味は英英定義・用例・コロケーション・文脈だけで
 * 与える。訳語フィールドを足したくなった時点で第一目的から外れている。
 */

/** 学習レベル。1 が入門、6 が TOEIC 990 / TOEFL 120（満点）相当。 */
export type LevelId = 1 | 2 | 3 | 4 | 5 | 6;

export const LEVEL_IDS: readonly LevelId[] = [1, 2, 3, 4, 5, 6] as const;

/** 品詞。学習上の区別に必要な粒度だけを持つ。 */
export type PartOfSpeech =
  | "noun"
  | "verb"
  | "adjective"
  | "adverb"
  | "phrase"
  | "preposition"
  | "conjunction";

/** 語のレジスター。同義語の使い分けを英語のまま学ぶために持つ。 */
export type Register = "neutral" | "formal" | "informal" | "academic" | "literary";

/** 単語エントリ。 */
export interface WordEntry {
  /** 安定 ID。`w-l<level>-<連番>` 形式。進捗レコードの外部キーになるため変更しない。 */
  readonly id: string;
  readonly headword: string;
  readonly pos: PartOfSpeech;
  /** IPA。音声再生できない環境での手掛かりとして持つ。 */
  readonly ipa?: string;
  /** 英英定義。学習者の目標レベル以下の語彙で書く。 */
  readonly definition: string;
  /** 用例。最低 1 件。定義だけでは決まらない使われ方を示す。 */
  readonly examples: readonly string[];
  /** よく共起する形。単語単体ではなく塊で覚えるために持つ。 */
  readonly collocations?: readonly string[];
  readonly synonyms?: readonly string[];
  readonly antonyms?: readonly string[];
  readonly register?: Register;
  readonly level: LevelId;
}

/** パッセージのジャンル。設問の作り方が変わるため型で区別する。 */
export type PassageGenre = "narrative" | "expository" | "academic" | "conversation" | "news";

/** パッセージ内の穴埋め設問。 */
export interface ClozeItem {
  readonly id: string;
  /** 空所に入る正解。パッセージ本文中に `{{1}}` の形で位置を示す。 */
  readonly answer: string;
  /** 選択肢（正解を含む）。表示順はランタイムでシャッフルする。 */
  readonly options: readonly string[];
  /** 英語のヒント。訳ではなく、意味の言い換えや文法的な手掛かりを書く。 */
  readonly hint: string;
}

/** 読解設問。 */
export interface ComprehensionQuestion {
  readonly id: string;
  readonly prompt: string;
  readonly options: readonly string[];
  /** `options` 内の正解位置。 */
  readonly answerIndex: number;
  /** 正解の根拠。本文のどこからそう読めるかを英語で書く。 */
  readonly rationale: string;
}

/** パッセージエントリ。 */
export interface PassageEntry {
  readonly id: string;
  readonly title: string;
  readonly level: LevelId;
  readonly genre: PassageGenre;
  /** 本文。段落は空行区切り。穴埋め位置は `{{1}}` `{{2}}` … で示す。 */
  readonly text: string;
  /** 本文中の難語に対する英英の注。訳語は置かない。 */
  readonly glossary?: readonly { readonly headword: string; readonly definition: string }[];
  readonly clozes: readonly ClozeItem[];
  readonly questions: readonly ComprehensionQuestion[];
}

/** レベル定義。TOEIC / TOEFL のスコア帯との対応を持つ。 */
export interface LevelDefinition {
  readonly id: LevelId;
  /** 短い英語名。UI に出す。 */
  readonly name: string;
  /** CEFR 対応。 */
  readonly cefr: "A1" | "A2" | "B1" | "B2" | "C1" | "C2";
  /** TOEIC L&R のおおよそのスコア帯。 */
  readonly toeic: readonly [number, number];
  /** TOEFL iBT のおおよそのスコア帯。A1/A2 は TOEFL の測定範囲外のため null。 */
  readonly toefl: readonly [number, number] | null;
  /** このレベルで扱う累積語彙規模の目安（headword 数）。 */
  readonly targetVocabulary: number;
  /** レベルの到達像。英語で書き、学習画面にそのまま出す。 */
  readonly canDo: string;
  /** UI のアクセント色（Tailwind ではなく CSS 変数として渡す RGB 値）。 */
  readonly accent: string;
}
