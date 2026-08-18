---
description: "Use when: working on PRs, pushing code, handling AI review, Copilot/Claude レビュー対応, review loop, CI monitoring, git push, PR作成, レビューコメント対応, review feedback, pull request workflow. CRITICAL workflow rule for PR completion pipeline."
applyTo: "**"
---
# AI レビューループ — 絶対スキップ禁止ルール

> **プロジェクトオーナー決定。変更はオーナーの明示認可がある場合のみ
> （直近認可: 2026-07-30 DEC-20260730-005）。**

## 最重要ルール（5つ）

1. **PR に push したら、必ず AI レビュー（Copilot または Claude repo-aware fallback）の到着を待つ**（最大20回・最大20分相当。同期 `sleep` ループは禁止）
2. **AI レビューは Round 3 を既定の収束点とする**（Round 3 後の非ブロッキング
   Must/Should は Backlog 化する。即時ブロッカー、必須ゲート失敗、anchor 前の
   merge 契約 preflight 失敗、
   または人間オペレーターの明示指示がある場合だけ、証跡付きで Round 4 以降へ延長する。
   各 push 後に Copilot を明示発火し、Copilot 不可時は `ai-review-fallback.yml` または同等の
   Claude repo-aware review を実行する。受理可能な証跡は Copilot / Claude / Codex の
   ANY-OF とするが、Codex は自動発火しない）
3. **「レビュー待ちは省略」「次に進む」は絶対禁止**（下記「ラウンド外 push」の 2 例外を除く）
4. **push 直後にレビュワー設定確認を必ず行う**（Copilot AI が reviewer に設定されているか確認。未設定または利用不能なら `request_copilot_review` または Claude repo-aware fallback を即実行）
5. **レビュー検出は `commit_id` ではなくスレッドベースで行う**（`get_review_comments`
   で未解決または未返信のスレッド数を確認する。`commit_id` フィルタリングは禁止）

## ラウンド外 push（2026-07-03 オーナー認可・DEC-20260703-002）

次の push は**ラウンドを消費せず、レビュー再リクエスト・証跡再 pin も不要**とする:

- **直下のレビュー証跡（`docs/ai/reviews/*.json`）だけの push**（diff fingerprint は
  証跡を除外して計算し、`review_head_is_compatible` が証跡専用 commit を許容するため。
  `README.md`、非 JSON、サブディレクトリはレビュー証跡専用 path に含めない。実測:
  証跡再 pin が再レビューを誘発する往復が PR #682 で発生）
- **green tier（`ai/operation-policy.yml` の `release_to_main` 分類で green と判定された PR。red_if_touches 非接触であっても yellow 判定の変更は対象外）で、Round 1 のレビューが Must/Should 0 件かつ未解決または未返信の active thread 0 件のとき**は、以降のラウンド発火は不要（CI green と `ci/final-gate` 成功はこの短絡と無関係に必須のまま）。**短絡後に直下の `docs/ai/reviews/*.json` 以外の差分を push した場合は短絡は失効し、その push からラウンドを再開する（Round 2 扱い・通常の再リクエスト必須）**。yellow / red は本短絡の対象外。

## 長時間待機の実行制約（停止事故防止）

- VS Code / Copilot Chat / Orchestrator 環境では、`sleep` を含むシェルポーリングループを同期実行してはならない。
- CI / AI レビュー待機は省略しない。ただし実行方法は、1回ごとの状態確認コマンドを短いタイムアウトで実行し、次回確認はエージェント側の進行管理で行う。
- 端末コマンドが失敗した場合は、読み込み状態を継続せず即座に exit code・stderr・対象コマンドを報告し、原因修正へ戻る。
- 5分以上同じ待機状態が続く場合は、プロセス一覧と直近ログを確認し、滞留プロセスを止めてから再開する。
- Claude Code Remote 等の webhook 対応環境では、`subscribe_pr_activity` 等のイベント受信を優先し、シェル滞留を作らない。

## レビュワー設定確認ルーチン（push 後に毎回実行）

```text
push 後:
  1. PR が Draft 状態でないことを確認する（Draft 状態ではレビューが発火しない）
     - Draft の場合: gh pr ready <PR番号> で Ready for review に変更してからリクエスト
  2. 【Round 1】request_copilot_review で明示的にレビューをリクエストする。Copilot が利用不能なら ai-review-fallback.yml または Claude repo-aware review を実行する
  3. ポーリング初回（1回目）と以降5回ごと（5, 10, 15, 20回目）にレビュワー設定を再確認する
```

> **なぜ必要か**: Copilot AI は push イベントでは自動発火しない（2026年5月以降のルール変更）。
> 各 push 後に必ず `request_copilot_review` で明示リクエストを実行すること。
> PR が Draft 状態のままではレビューが発火しないため、状態確認が必須。

### Copilot 再依頼の正しい経路（2026-07-25 実測・3経路中2つが silent no-op）

`request_copilot_review` ツールが使えず CLI / API で直接 Copilot レビューを再依頼する場合、**確実に成立するのは次の 1 経路のみ**である。

```bash
gh pr edit <PR番号> --add-reviewer copilot-pull-request-reviewer
```

以下の 2 経路は **成功レスポンス（200 系・エラー無し）を返しながら実際にはレビュワーが登録されない（silent no-op）**。レスポンスだけを見て「依頼できた」と判断してはならない。

- GraphQL `requestReviews(botIds: [...])`
- REST `POST /repos/{owner}/{repo}/pulls/{pull_number}/requested_reviewers`

依頼が成立したかどうかは、レスポンスではなく `gh pr view <PR番号> --json reviewRequests` 等で実際に `copilot-pull-request-reviewer` がレビュワー一覧に入っているかを確認して判定する。本注記が無い状態で 4 PR にわたり誤った経路（GraphQL/REST）を使い続け、レビューが依頼されないまま進行した実障害があるため、明文化する。

Claude repo-aware fallback を実行する場合、通常実装では `repository_context_mode=related_context` 以上、
workflow / security / release / red risk では `full_repo_agentic` を満たすレビュー証跡を要求する。
`diff_only` は docs-only / data-only / typo 等の低リスク変更で理由を記録した場合だけ許可する。
正常なレビュー結果が得られた同一 `head_sha` / diff fingerprint / context fingerprint で、
同一 provider の repo-aware fallback を重複実行しない。未到着または timeout の再発火は
例外だが、provider 実行前に次の receipt を PR コメントへ投稿する。

```text
<!-- ai-review-attempt:v1 round=N head=<40sha> -->
```

同じ round と head では初回 1 件と再発火 3 件の計 4 件までとし、5 件目の provider 実行を
拒否する。receipt は信頼済みオペレーターまたは自動化 actor が投稿したものだけを数える。
1 回の workflow 発火では direct Anthropic API（`claude`）だけを実行する。複数 provider
の選択・並列実行・failover は行わない。
context budget は related files 40 件、prompt context 180000 文字、file excerpt 12000 文字、
探索 command 12 件を既定上限とし、超過時は relevance score 順に切り詰めて証跡化する。

## レビュー検出ルーチン（2段階検出）

```text
ポーリングループ各回:
  段階1: gh api --paginate（CLI）または pull_request_read（MCP, perPage=100）で AI レビュー総数（Copilot / Claude / 明示実行した Codex 証跡）が増加したか確認
    - CLI: gh api --paginate を使う / MCP: perPage=100 を指定する（デフォルト per_page=30 でページネーション漏れが発生する）
    - commit_id でフィルタリングしない（API 仕様で一致しないことがある）
    - ユーザー名: Copilot は copilot-pull-request-reviewer[bot]、fallback は本文 `## AI レビュー結果` を検出対象にする
  段階2: get_review_comments（スレッドベース）で未対応コメントを確認
    - active thread: isOutdated=false かつ AI レビューコメントを含む
    - 完了条件: isResolved=true かつ、最新 AI コメントより後に trusted operator の返信がある
    - 未完了条件: isResolved=false または、最新 AI コメントより後の trusted operator 返信がない
    - isOutdated=true の thread は未完了件数から除外する
    - ユーザー名: Copilot / Claude repo-aware fallback の author、または明示実行した Codex を含む `## AI レビュー結果` marker を検索対象にする
  → いずれかで検出されれば「レビュー到着」と判定
```

> **なぜ必要か**: `get_reviews` + `commit_id` フィルタリングだけでは、ページネーションや
> API 仕様の問題でレビューを見落とすことがある。実際に PR #36 でレビューが届いていたのに
> デフォルト per_page=30 のページネーション漏れで検出できず、「未到着」と誤判定した。
> また、レビュー本体(`copilot-pull-request-reviewer[bot]`)とコメント(`Copilot`)で
> ユーザー名が異なるため、両方を検索対象にすること。

## レビューループの手順（既定 Round 1〜3）

```text
push 後（Round N, N=1..3）:
  0. 【必須】PR が Draft でないことを確認する（Draft なら gh pr ready で変更）
     - formal Round N の開始前に、正規化後も異なるレビュー済み head が N-1 件あり、
       すべて現在 head の直系 ancestor であることを確認する
  1. 【Round N レビュー発火】request_copilot_review で明示的にレビューをリクエストする。Copilot が 422 / quota / timeout / 未到着の場合は Claude repo-aware fallback を実行する
     → push 後の自動発火は期待しない。receipt を先に投稿し、1 attempt につき
       Copilot または Claude の 1 provider だけを明示実行すること
  1b. AI レビュー到着を確認（最大20回、2段階検出。同期 `sleep` ループは禁止）
  2. レビューコメントを取得・分類（Must/Should/Nice）
  3. plan.md の AC と照合し、AC と矛盾する指摘は AC 優先で対応不要と判定
  4. Must/Should を修正（AC 準拠）
  5. 【必須・Hookリマインド】各コメントに GitHub 上で返信する
     - add_reply_to_pull_request_comment で全スレッドに対し修正内容・見解を返信
     - 返信なしのまま `task_complete` / セッション終了 / release judgement に
       進もうとした場合、Hook（pre_task_complete_guard, stop_review_guard）が
       未返信スレッド数を検出してリマインドする。N-371 / P-064 により、
       block は実 CI 失敗と full-plan safety の fail-close 判定に限定する
     - 「修正済み」「対応不要と判断（理由:〜）」等、簡潔でよいので必ず返信すること
  6. targeted test / lint / typecheck を実行（フル CI は Round 3 後または Must/Should 0 件の最終ゲートへ集約）
  7. コミット・プッシュ（N < 3 かつ Must/Should > 0 の場合のみ）
  8. N < 3 なら次ラウンド（N+1）へ、N = 3 なら Round 3 処理へ

Round 3 処理:
  - Must/Should が残る場合は即時ブロッカーか非ブロッキングかを分類
  - 即時ブロッカー（P-001/P-002/P-003、秘密情報、実資金安全、CI failure、データ破壊等）は
    merge を fail-close し、安全に是正可能なら下記「延長ラウンド」へ進む
  - 非ブロッキング指摘は Backlog に記録し、PR コメントに Backlog ID と残リスクを返信
  - 延長条件がなくレビューが収束した場合だけ、レビュー結果を `docs/ai/reviews/*.json` へ
    記録して terminal evidence anchor へ進む

延長ラウンド（Round N, N>=4）:
  0. `ai/operation-policy.yml` の `review_policy.push_review_loop.extension_rounds` を照合する
  1. prior round 完了、material correction、非証跡変更、targeted validation 成功、
     進捗と認可の証跡をすべて満たしたうえで、次のいずれかを満たす場合だけ適格とする:
     - 人間オペレーターが同じ PR での追加変更を明示した
     - 即時ブロッカー、必須ゲート失敗、anchor 前の merge 契約 preflight 失敗の是正が必要
  2. 非ブロッキング Must/Should/Nice だけ、レビュー再依頼だけの空 commit、前の reviewed
     head から実質差分がない場合は延長しない
  3. push 前に targeted validation を通し、先に material correction commit C を作る。
     その commit を `corrected_head_sha` として、下記の exact 8 項目だけを持つ JSON object
     を execution ledger の `review-extension-evidence` fence へ 1 件追記し、
     ledger だけの evidence commit E を続ける。`corrected_head_sha` を同じ commit 自身へ
     埋め込まない。reviewed head と次 round の `prior_reviewed_head_sha` は E とする。
     C と E の間に別 commit を置かず、E に別 path を混ぜない
  3b. 全プラン実行モードでは E 作成後、gitignore 対象のローカル状態
     `.github/full-plan-execution.flag` を更新する。flag を commit、stage、force-add
     してはならない。flag の `extension_records` は HEAD の execution ledger にある
     延長 block の末尾列と同値にし、`current_round`、`extension_authorization_ref`、
     `corrected_head_sha` を最新記録、認可参照、直前の C に一致させ、
     `extension_active=true` とする。既存記録は変更せず、末尾へ 1 件だけ追加する。
     commit graph と ledger は `review_report_gate.py`、ローカル flag との同期は
     `full_plan_completion.py` が別々に fail-close で検証する
  3c. push 後、review request 前に同じ 8 項目を PR コメントへ記録する
  4. `trigger_evidence` は `STABLE-ID: 具体的根拠` 形式とする。同じ外部 finding、
     check、thread には同じ stable ID を使い、branch suffix 等による ID ローテーションで
     上限を回避しない。同一 stable ID は延長記録 3 件までとし、4 件目を拒否する。
     `authorization_ref` は `DEC-YYYYMMDD-NNN` 形式で、evidence head の
     `docs/ai/decision-ledger.md` に同名見出しが実在しなければならない。
  5. 既存の Round N 手順どおり receipt を投稿し、レビューを明示発火する。到着、分類、
     最新 AI コメント後の trusted operator 返信、thread 解決を確認する
     - fallback がレビュー後に証跡 commit R を作る場合、変更できるのは直下の
       `docs/ai/reviews/*.json` だけである。R は E と次の C の間にだけ置ける
     - canonical sequence は `P3 → C4 → E4（reviewed/pinned prior）→ R4* → C5 → E5`
       である。次 round の prior と最終 review report の `head_sha` は R ではなく E とする
     - 全プラン実行モードではレビュー収束後、ローカル flag の記録を保ったまま
       `extension_active=false` へ戻す。この遷移も commit には含めない
  6. Round N で新しい適格トリガが判明した場合だけ Round N+1 へ進む。同一指摘の修正試行
     3 回、連続再トリガー 3 回、進捗ゼロ、ポリシー違反、回復不能な認証／競合で停止する
  7. 最終レビュー証跡の `review_round` と `round_extensions[]` に全延長ラウンドを記録する
```

延長記録の canonical block は次の形とする。8 項目と
`verification_commands` 内の 3 項目以外を加えず、秘密情報を記録しない。

````markdown
```review-extension-evidence
{
  "round": 4,
  "trigger_kind": "explicit_human_instruction",
  "trigger_evidence": "USER-20260730-001: Round 4 の実施を人間オペレーターが明示した",
  "authorization_ref": "DEC-20260730-005",
  "prior_reviewed_head_sha": "<Round 3 のレビュー済み40桁SHA>",
  "corrected_head_sha": "<targeted validation 済みの C の40桁SHA>",
  "progress_summary": "延長契約を正本と実行経路へ反映した。",
  "verification_commands": [
    {
      "command": "uv run python scripts/check_review_round_cap.py --default-rounds 3 --fail-on-unqualified-extension",
      "exit_code": 0,
      "summary": "既定 Round 3 と条件付き延長の整合検査が成功した"
    }
  ]
}
```
````

provider 実行前の safety preflight は、追加行の秘密情報らしい値と credential 系 path、
リポジトリ境界を検査する狭い P-002/path 検査である。P-001/P-003 の意味検査や P-002
全体を証明しない。通過後も `policy_check`、secret scan、対象テスト、最終 CI を省略しない。

## レビュー収束後の terminal anchor

Round 1〜3 のどこでレビューが収束しても、release judgement より先に次を 1 回だけ行う。

1. レビュー結果を `docs/ai/reviews/*.json` へ反映する。
2. その path だけを変更する最終 commit を作り、件名を
   `[PR #<PR番号>; commits=<anchor自身を含むPR総commit数>]` で終える。
3. anchor を push した時点で、それ以前の `ci_final_gate_passed` を未達へ戻す。
4. anchor head で全 CI、`ci/final-gate`、review report、未解決または未返信の active
   thread 0 件を
   取り直す。anchor push では新しい review request を出さず、新 review round に数えない。
5. その同じ head だけを `release-judgement` skill へ渡す。

anchor に別 path が混ざった場合、件数が GitHub の実 commit 数と違う場合、または
anchor 後に commit が増えた場合は merge しない。コード／仕様変更が必要になった場合は、
途中 anchor を残して新しい anchor を追加せず successor PR へ切り出す。延長ラウンドは
terminal anchor 作成前に限り、anchor は全延長が収束した後に 1 回だけ作る。

terminal state-sync PR も同じ review loop を使う。先頭の state-sync commit 後に
Must／Should 対応が必要なら、plan／checkpoint／execution ledger／pre-PR 証跡の
許可範囲に収まる追加 commit として積む。最終形は「1 件以上の sync series + anchor」とし、
anchor の `commits=M` へ追加 commit を含む実総数を書く。2 commit へ戻すための
squash／force-push は要求しない。許可外 path が必要な指摘、または anchor 後の指摘は
terminal state-sync のまま処理せず fail-close する。

## ラウンド予算と停止条件

**既定は 3 ラウンド**とし、Round 4 以降には固定の総ラウンド上限ではなく、ラウンドごとの
適格性・進捗・証跡を要求する。以下の条件で自動対応を停止し人間にエスカレーションする：

- **Round 3 到達**: 非ブロッキング指摘は Backlog に記録して PR コメントに返信し、
  延長しない。即時ブロッカーまたは必須ゲート失敗があり、安全に是正できる場合だけ
  証跡付き延長へ進む。是正不能なら fail-close する
- **Round 4 以降の不適格な継続**: 非ブロッキング指摘だけ、Nice だけ、空 commit、
  実質差分なし、認可・発火理由・検証・修正前後 head の記録不足では追加 push／review
  request を行わない
- **進捗ゼロ**: 前の reviewed head から material correction がない、または同じ状態を
  再提出する場合
- **同一指摘の繰り返し**: 同じコメントへの修正試行が3回を超えた場合
- **同一 stable ID**: 同じ外部 finding、check、thread を示す stable ID の延長記録が
  3 件に達した場合。ID の付け替えで継続しない
- **連続再トリガー**: 同一 round/head の receipt が初回 1 件 + 再発火 3 件に達した場合。
  5 件目の provider 実行を行わない
- **ポリシー違反**: P-001〜P-003 違反を検出した場合
- **コンフリクト/認証不能**: マージコンフリクトまたは認証エラーで継続不能な場合
- **進捗ゼロ override 要求の繰り返し（B-368）**: 「直近の状態取得結果 == 前回の状態取得結果」かつ「直近の応答 == ユーザへの override 要求」を **2 回連続**検出した場合、3 回目は override 要求を出さず、(a) 具体的な不足作業の自動実施、(b) escalation reason への従順、(c) `AskUserQuestion` での選択肢提示 のいずれかへ切替える

## よくある間違い（過去10回以上発生）

- ❌ 「レビューコメントに返信したので完了」→ 返信後も新レビューを待つ
- ❌ 「CI が通ったので完了」→ CI 通過後も AI レビューを待つ
- ❌ 「push したので次のステップへ」→ push 後は必ずレビュー待ち
- ❌ 「時間がかかるので省略」→ 省略は絶対禁止
- ❌ 「commit_id でフィルタしたらレビュー0件」→ commit_id フィルタは禁止。スレッドベースで確認する
- ❌ 「push したら自動でレビューが来る」→ Copilot は push 自動発火しない。必ず `request_copilot_review` または Claude repo-aware fallback で明示実行
- ❌ 「Draft PR にレビューをリクエストした」→ Draft 状態ではレビューが発火しない。`gh pr ready <PR番号>` で Ready に変更してからリクエスト
- ❌ 「20分待ってもレビューが来ない」→ Draft 状態でないか確認。Draft なら Ready に変更してリクエスト
- ❌ 「修正だけして返信せずに push」→ 返信は必須。N-371 以降の Hook はレビュー儀式をリマインドするが、返信なしを完了扱いにしてよいという意味ではない
- ❌ 「Round 3 なので必要な是正も行わない」→ 非ブロッキング指摘は Backlog 化するが、
  即時ブロッカー、必須ゲート失敗、anchor 前の merge 契約 preflight 失敗、
  人間オペレーターの明示指示は
  適格性と進捗を記録して Round 4 以降で是正する
- ❌ 「Round 4 以降は必要になるまで何度でも回す」→ 各ラウンドに material correction と
  8 フィールドの証跡が必要。非ブロッキング指摘だけ、空 commit、進捗ゼロでは延長しない
- ❌ 「状態が変わらないのに同じ override 文言を3回以上要求」→ 進捗ゼロでの override 要求は **2 回まで**（B-368）。3 回目は不足作業の自動実施・escalation reason 従順・選択肢提示へ切替える

## 自動 Hooks（`.github/hooks/review-loop-guard.json`）

以下の Agent Hooks は、安全床の fail-close とレビュー儀式のリマインドを担当する。N-371 / P-064 により、レビュー未到着・未返信・Round 予算超過・一時的な lookup 失敗は block せず、追加コンテキストで継続手順を促す。

| Hook | イベント | 動作 |
| ---- | -------- | ---- |
| `pre_task_complete_guard.py` | **PreToolUse** | `task_complete` / release judgement 時に PR 状態をチェック。実 CI 失敗または full-plan safety 未達なら `permissionDecision: "deny"` でブロックし、レビュー未完了はリマインド |
| `post_push_reminder.py` | **PostToolUse** | `git push` 後に `decision: "allow"` で CI 確認 + レビュー待機をリマインド |
| `stop_review_guard.py` | **Stop** | 実 CI 失敗または full-plan safety 未達ならセッション終了をブロックし、レビュー未完了はリマインド |
| `pre_compact_context.py` | **PreCompact** | コンテキスト圧縮前にレビューループルールを systemMessage として注入 |

追加の全プラン実行モード強制:

- `.github/full-plan-execution.flag` が存在し `active=false` でない場合、`pre_task_complete_guard.py` と `stop_review_guard.py` は `full_plan_completion` の安全判定（plan 状態・成果証跡・demo/G18）を fail-close で確認する。OPEN PR のレビュー儀式状態は N-371 によりリマインド扱いとする。
- 全プラン実行モードでは、anchor head の未解決または未返信の active thread 0 件、
  CI 全 pass、
  release judgement の承認だけでは完了ではない。merge 前契約検査、自動マージ、実終端照合、
  main pull、次 branch の plan／checkpoint／ledger 同期までが完了条件である。

> **注意**: Stop hook には `stop_hook_active` による無限ループ防止が組み込まれている。
> VS Code は Stop hook がブロック → エージェント続行 → 再度 Stop の場合、2回目の入力に
> `stop_hook_active: true` を **自動付与** する。2回目は必ず終了を許可する。
> 2回目の Stop では終了が許可される。

## メモリ参照

persistent memory はハーネスが自動提供するもの（Claude Code の auto-memory 等）を用いる。旧 `/memories/repo/` / `/memories/session/` パスは本リポジトリの標準環境に存在しない（2026-07-03 是正・DEC-20260703-002。`/memories/` マウントを持つ環境でのみ配下を確認する）。
