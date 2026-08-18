# Shogun 安全境界（参照のみ・複製しない）

本書は Shogun 運用モデル（[shogun-operating-model.md](shogun-operating-model.md)）が従う安全床の**参照一覧**である。安全床の定義本体はここに複製・再定義しない（drift 防止）。各項目の正本が常に優先する。

## 参照する正本

| 安全床 | 正本 | 要点 |
| --- | --- | --- |
| Hard-stop（絶対停止） | `ai/operation-policy.yml` `safety.hard_block` | secret/token 露出・P-001/P-002/P-003 違反・破壊的 DB 操作・重要な安全装置のバイパス・保護ブランチ危険 push・CI 全チェック失敗等は dispatch の如何にかかわらず停止 |
| red 判定パス | `ai/operation-policy.yml` `release_to_main.red_if_touches` | 例: `src/**/migrations/**` / `.github/workflows/**` / `pyproject.toml` / ロックファイル / `.env*` / `configs/**`（プロジェクト固有の対象は `project-config.yml` で定義） |
| risk tier | `ai/operation-policy.yml` `release_to_main.tiers` | green / yellow / red のレビュー・CI・manifest 要件 |
| 重要な本番操作の有効化 | `project-config.yml` の `policies`（プロジェクト固有の重要操作定義）+ `ai/operation-policy.yml` `safety.hard_block` | プロジェクトごとに定義する重要操作（例: 実発注・実課金・本番切替等）は、性能評価・検証後に人間が明示認可するまで有効化しない。安全装置バイパスは hard_block・AI は自律有効化しない |
| 自動マージの唯一の例外 | `docs/adr/` の重要判断記録 | 全プラン実行モードで release-manager 承認後のみ |

## Shogun 固有の境界

- **lease 不可項目**：secret 値出力・実資金の移動・重要な安全装置の緩和（project-config.yml の policies で定義する重要操作を含む）は、どのような lease 表現でも認可されない（`ai/operation-policy.yml` `capability_lease.not_leasable`）。
- **Skill 三分類**：red / hard_block 該当 skill は自律作成・自律有効化しない。
- **判断不能時**：安全側へ倒す（P-010 フェイルクローズ）。dispatch 中に hard_block / red_if_touches への接触が必要と判明したら、その packet を停止し人間判断へ分類して報告する（`verify_safety_floor` step）。
