---
name: release-judgement
description: PR の対象 head に対する受入条件、CI、レビュー、未解決 thread、ロールバック可能性を独立確認し、マージ可否を判定する。terminal anchor 後の最終判定、merge 前契約検査へ渡す判定証跡（Evidence Schema v2 release bundle）が必要なときに使う。
---

# リリース判定

対象 head の証跡だけを使い、`MERGE` または `REJECT` を返す。コード変更とマージは行わない。

## 実行条件

実装から独立したコンテキストで実行する。モデル名は設定せず、通常は `capability_floor=high`、`effort=high`、`budget_strategy=single-strong` を使う。red または証跡に矛盾がある場合は `dual-check` とし、実行環境が対応するなら別モデル系列または別プロバイダーで照合する。

最高位の能力は、判定を分ける曖昧さ、矛盾した証跡、未解決の重大 finding がある場合だけ使う。独立した実行環境を確保できない場合は `REJECT` とする。実モデル名が公開された場合は `resolved_runtime` の観測値として残すだけにし、固定設定へ転用しない。

## 確認

1. GitHub から PR 番号と最新 head SHA を判定直前に取得する。
2. terminal anchor、最終 CI、正式レビュー証跡が同じ head を対象としていることを確認する。
3. User Intent と適用対象の受入条件を列挙し、実装、テスト、証跡との対応を確認する。
4. Must finding、実 CI failure、未解決または未返信の active thread がゼロであることを確認する。
5. risk tier に必要なレビュー独立性、秘密情報検査、ロールバック手順、merge 前契約の前提を確認する。
6. Round 3 後に繰り延べた非ブロッキング項目には、Backlog ID、残リスク、最小封じ込めがあることを確認する。

一つでも確認できない項目があれば fail-close で `REJECT` とする。別 head の CI、古いレビュー、`agmsg` の助言だけを承認根拠にしない。

## 判定証跡（Evidence Schema v2 release bundle・必須）

DEC-20260813-002 により release 経路も v2 が既定 must である。terminal anchor 後は PR branch へ commit を追加できないため、判定証跡は PR branch ではなく判定 comment に同梱する（bundle 方式）。次の 3 工程を判定ごとに必ず行い、どれか一つでも失敗したら `MERGE` を宣言しない。

1. **生成**: 判定対象 head の checkout（`git rev-parse HEAD` が anchor head と一致）で、判定に実際に使う検証 command を spec file に列挙し、generator で `artifact_kind=release` の artifact と command manifest を生成する。

   ```bash
   uv run python scripts/ai/release_evidence_bundle.py --generate \
     --spec-file <spec.json> --pr <PR番号> --head-sha <小文字40桁SHA> \
     --decision <MERGE|REJECT> --out-root <staging dir> --cwd <対象 head の checkout>
   ```

   spec file は `{"commands": [CommandSpec 相当], "pass_env": [...]}` で書く。最低限の verification（network 不要）は、レビュー済み head が判定 head の祖先であること（`git merge-base --is-ancestor <reviewed_head> <head>`）と、anchor tree 上での review report gate（`scripts/ai/review_report_gate.py`）の再実行とする。例:

   ```json
   {
     "commands": [
       {"command_id": "reviewed-head-is-ancestor", "role": "verification", "claim_type": "exit_success",
        "argv": ["git", "merge-base", "--is-ancestor", "<reviewed_head>", "<head>"]},
       {"command_id": "review-report-gate", "role": "verification", "claim_type": "exit_success",
        "argv": ["<python>", "scripts/ai/review_report_gate.py", "--root", ".", "--base-ref", "<baseRefOid>",
                 "--head-ref", "<head>", "--changed-only", "--required-providers", "<accepted providers>"],
        "timeout_ms": 120000}
     ],
     "pass_env": []
   }
   ```generator は sanitized environment（`LANG` / `LC_ALL` / `TZ` / `PATH` のみ）で command を実行し、名前に `TOKEN` / `SECRET` / `PASSWORD` / `API_KEY` / `CREDENTIAL` などを含む環境変数を拒否する。gh 依存 command（`ci/final-gate` status・active thread 数）を manifest に入れる場合は `pass_env` に `HOME` だけを書き、gh の file auth（`gh auth status` が `hosts.yml` の path を示す状態）で実行する。token 環境変数（`GH_TOKEN` / `GITHUB_TOKEN`）は渡さない。file auth が使えない環境では gh 依存 command を manifest に入れず、その確認は artifact 外で行ったことを報告 `checks` に明記する（同じ head の CI／thread は autopilot と merge 前契約検査が gh で再確認する）。`--decision MERGE` で verification が 1 件でも fail すると exit 1 になり、その artifact は MERGE の根拠にできない。

2. **検証と描画**: 生成物から判定 comment を描画し、描画結果そのものを bundle validator に通す。validator は内部で `scripts/ai/validate_release_evidence.py` の共通 core（`--marker` / `--pr` / `--head-sha` 相当の binding 検査を含む）を呼ぶ。`MERGE` は exit 0（`ok: true`）が必須である。`REJECT` は verification の fail を正直に記録した artifact でよく、core の `result=fail` 由来で exit 1 になり得るが、出力 JSON の `bundle_structure_ok` が `true`（`bundle_*` / `evidence_*` / `manifest_*` の構造違反なし）でなければ描画からやり直し、投稿しない。

   ```bash
   uv run python scripts/ai/release_evidence_bundle.py --render \
     --artifact <staging>/docs/ai/evidence/<YYYY-MM-DD>-pr<PR番号>-release-v2.json \
     --manifest <staging>/docs/ai/evidence-manifests/<sha256>.json \
     --pr <PR番号> --head-sha <小文字40桁SHA> --output <comment.md>
   uv run python scripts/ai/release_evidence_bundle.py \
     --comment-file <comment.md> --pr <PR番号> --head-sha <小文字40桁SHA>
   ```

3. **投稿**: 投稿権限と trusted actor 条件を満たす場合だけ、描画した comment 本文をそのまま PR issue comment として投稿する（`gh pr comment <PR番号> --body-file <comment.md>`）。comment には次が含まれる。

   ```text
   <!-- release-manager-decision:v1 pr=<正のPR番号> head=<小文字40桁SHA> decision=<MERGE|REJECT> -->
   <!-- evidence-v2 kind=release path=docs/ai/evidence/<YYYY-MM-DD>-pr<PR番号>-release-v2.json sha256=<artifact sha256> -->
   <!-- release-evidence-bundle:v1 pr=<正のPR番号> head=<小文字40桁SHA> -->
   ```

   その直後に fenced ```json ブロック 2 つ（artifact 本体・command manifest 本体）が続く。報告 YAML など人間向け本文は bundle の後ろに置く。marker の PR と head には判定直前の実値を入れる。投稿に失敗した場合や actor 条件を確認できない場合は、判定証跡の作成完了と報告しない。

merge 後の archive: bundle は次の通常 PR（次 task の先頭 sync commit）で `uv run python scripts/ai/release_evidence_bundle.py --comment-file <comment 本文> --pr <PR番号> --head-sha <SHA> --archive-root .` を実行し、`docs/ai/evidence/<YYYY-MM-DD>-pr<PR番号>-release-v2.json` と `docs/ai/evidence-manifests/<sha256>.json` を materialize して commit する。terminal state-sync PR は sync commit の path scope に `docs/ai/evidence/**` を含められないため、その場合は直後の別 PR で archive する。

## 互換 marker（単独では MERGE 根拠にならない）

`release-manager-decision:v1` は役割名ではなく既存の自動化が読む互換用の wire marker であり、上記 bundle の 1 行目として維持する。marker 単独の comment は `scripts/ops/pr_autopilot.sh` と `scripts/ai/validate_rebase_merge_contract.py` の両方が MERGE 根拠として認めない（bundle 不在・sha 不一致・head 不一致・`result!=pass` の MERGE はすべて fail-close）。このスキルは comment 投稿後もマージしない。ユーザーが全プラン実行または対象 PR のマージを明示的に認可している場合に限り、上位の Orchestrator が後続の契約検査とマージを行う。

## 報告

```yaml
pr: 正のPR番号
head_sha: 小文字40桁SHA
risk_tier: green | yellow | red
runtime_profile:
  capability_floor: high | top
  effort: high | inherit
  independence: separate-context | separate-model-family | separate-provider
  budget_strategy: single-strong | dual-check
resolved_runtime: 実行環境が公開した範囲。非公開なら unavailable
checks:
  acceptance_criteria: pass | fail
  ci: pass | fail
  review_evidence: pass | fail
  active_threads: pass | fail
  rollback: pass | fail
release_evidence:
  path: docs/ai/evidence/<YYYY-MM-DD>-pr<PR番号>-release-v2.json
  artifact_sha256: artifact raw bytes の sha256（evidence-v2 marker と同値）
  manifest_sha256: command manifest の sha256（command_manifest_sha256 と同値）
  validator: pass   # release_evidence_bundle.py --comment-file の exit 0 を確認した場合だけ pass
findings: 判定を妨げる事実。なければ []
decision: MERGE | REJECT
marker_posted: true | false   # bundle 同梱 comment を投稿できた場合だけ true
```
