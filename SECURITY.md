# WorkBridge Japan — セキュリティおよび運用ポリシー (v1.8)

本ドキュメントは現バージョンのコードに基づく事実関係をまとめたものです。日本国内ホスティング・データ分離・暗号化・削除依頼対応・DPA / セキュリティチェックシートへの応答を求められた場合の参照資料として使用してください。

---

## 1. ホスティング

### 1.1 推奨デプロイ構成（本番）

| レイヤ | 採用サービス | リージョン |
|---|---|---|
| アプリケーションサーバ | Render Web Service / または Fly.io / GCP Cloud Run / AWS App Runner | 顧客要件に応じて選択 |
| データベース | **Supabase Postgres** ⭐ | **`Northeast Asia (Tokyo)` (ap-northeast-1)** |
| ファイルストレージ | アプリケーションサーバの永続ディスク (将来は Supabase Storage / S3 へ移行可) | 同 |
| 翻訳 API | Google Cloud Translation API | global (Google 管轄) |
| TLS 証明書 | Let's Encrypt 自動 | — |

### 1.2 日本国内ホスティング対応

**データベース（PII を含む全データ）**: Supabase Tokyo リージョンに常駐。データの物理的所在地は **東京** (AWS ap-northeast-1)。

**アプリケーションサーバ**: 顧客要件次第で選択可能:
- Render: 現在シンガポール（東京リージョン未提供）。日本ユーザーへの追加レイテンシ ≒ 80ms
- **Fly.io NRT (東京)** ⭐: 同一 Dockerfile で移行可能、日本国内デプロイ
- **GCP Cloud Run asia-northeast1 (東京)**: 同上
- **AWS App Runner / ECS Fargate (東京)**: エンタープライズ要件向け

要件に「サーバ自体も日本国内」が含まれる場合は、**Fly.io NRT** または **GCP Cloud Run 東京** への移行で対応します。コード変更不要、所要時間 1〜2 営業日。

### 1.3 翻訳 API について

Google Cloud Translation API への送信内容（管理者からスタッフへの指示テキスト等）は Google 管轄でグローバルに処理されます。`POST /api/v1/i18n/translate` 経由の送信が対象。

機微な内容を翻訳に出さないよう運用で配慮していただく必要があります（既定のスタッフ向け指示は業務指示が中心で、個人情報は通常含みません）。

将来オプション: 重要顧客向けにオンプレ翻訳エンジン (DeepL on-prem 等) への切替えに対応可能。

---

## 2. データ分離 (マルチテナント)

### 2.1 設計

全ての顧客データは `workspaces.id`（UUID）でスコープされており、データ取得を行う SQL は **必ず `WHERE workspace_id = ?` を含む**。アプリケーション層で実装。

該当テーブル:
- `workspaces`
- `workspace_staff_accounts`
- `staff_groups`
- `instruction_rounds` / `instruction_recipients` / `instruction_replies`
- `ws_presence`
- `workspace_chat_messages`
- `workspace_glossary_terms` / `workspace_expression_terms`
- `worker_glossary_saves`

### 2.2 認可

- **管理者**: JWT に `workspace_id` クレームが入っており、その workspace のデータにのみアクセス可能（`_require_admin_for_workspace` で workspace 不一致を 403）
- **スタッフ**: 同様に JWT に `workspace_id` がバインド。他社データには到達経路なし
- **総運営（運営会社）**: 全 workspace 閲覧可。`super_admin_password` で認証、`/api/v1/auth/super-assume` 経由で対象 workspace の管理者権限を一時発行

### 2.3 強化オプション: Postgres Row Level Security (RLS)

現在はアプリケーション層の `WHERE` 句で分離しています。エンタープライズ顧客から DB 層強制の要請がある場合、Postgres RLS により以下を導入可能:

```sql
ALTER TABLE workspace_staff_accounts ENABLE ROW LEVEL SECURITY;
CREATE POLICY ws_isolation ON workspace_staff_accounts
  USING (workspace_id = current_setting('app.workspace_id'));
```

これにより、コードバグや SQL インジェクションがあっても DB が他社データを返却しなくなる。導入工数 3〜5 営業日。

---

## 3. 暗号化

| 通信経路 / 保管場所 | 状態 |
|---|---|
| ブラウザ ↔ アプリケーションサーバ | **TLS 1.2+ 強制** (Render/Fly/Cloud Run 全て自動 HTTPS) |
| WebSocket (`wss://`) | 同上 (TLS で保護) |
| アプリケーションサーバ ↔ Supabase Postgres | **`sslmode=require`** で TLS 強制 |
| Supabase 内のデータ at-rest | **AES-256 暗号化** (AWS KMS, Supabase 標準) |
| アプリケーションサーバの永続ディスク | プラットフォーム標準暗号化 (Render / GCP / AWS) |
| パスワード | **bcrypt** ハッシュ (`passlib[bcrypt]`、コスト因子既定) |
| セッショントークン | **JWT (HS256)**、サーバ側に状態を持たない |

### 3.1 鍵管理

| 鍵 | 保管場所 | ローテーション |
|---|---|---|
| `SESSION_SECRET` (JWT 署名) | Render Environment Variable / Fly Secrets | 必要時に手動。ローテーション時は全ユーザー再ログイン |
| Supabase DB 接続文字列 | 同上 | Supabase 側で随時変更可 |
| Google サービスアカウント鍵 | Render Secret File | 顧客要請に応じてローテーション |
| `PORTAL_ADMIN_PASSWORD` / `SUPER_ADMIN_PASSWORD` | 同上 | 必要時 |

リポジトリ・GitHub に鍵が含まれていないことを保証（`.gitignore` で `google_key.json`, `.env` 除外、git履歴にも該当ファイルなし）。

---

## 4. 削除依頼への対応

### 4.1 ワークスペース単位の削除

```
DELETE /api/v1/workspaces/{workspace_id}?confirm=DELETE&admin_token=...
```

このエンドポイント 1 回呼び出しで以下が**全て不可逆に削除**されます:

| データ種別 | 削除内容 |
|---|---|
| ワークスペースメタ | `workspaces` 行 |
| 個人スタッフアカウント | `workspace_staff_accounts` 全行（ID・表示名・パスワードハッシュ・電話・メール） |
| グループ | `staff_groups` |
| 指示・受信記録・応答 | `instruction_rounds` / `_recipients` / `_replies` (FK CASCADE) |
| プレゼンス | `ws_presence` |
| 管理者 ↔ スタッフチャット | `workspace_chat_messages` |
| 拠点専用用語・表現 | `workspace_glossary_terms` / `_expression_terms` |
| スタッフが保存した単語 | `worker_glossary_saves` |
| アバター画像 | 管理者・スタッフのアップロード画像（`/static/uploads/`） |
| 指示画像 | 管理者がスタッフに送った画像 |

DB トランザクション内で実行され、いずれかの DELETE が失敗すれば全てロールバック。トランザクション成功後にファイルシステム掃除を best-effort で実施。

実行ログ（誰がいつ削除したか）は管理者操作ログとして 30 日間保存（要望に応じて拡張可）。

### 4.2 削除前のデータエクスポート

```
GET /api/v1/workspaces/{workspace_id}/export?admin_token=...
```

削除前のバックアップとして、ワークスペースの全データを 1 つの JSON ファイルでダウンロード可能。**パスワードハッシュは含まれません**（流出時の被害最小化）。

### 4.3 個別スタッフアカウントの削除

```
DELETE /api/v1/workspaces/{workspace_id}/staff-accounts/{account_id}?admin_token=...
```

個別スタッフ単位の削除（退職時等）。個人ログイン情報・アバターを削除。
過去の指示・応答ログは「誰宛に送られたか」記録として残ります（`staff_account_id` で紐付け）。これらの完全削除が必要な場合は別途相談ください。

### 4.4 翻訳 API ログ

Google Cloud Translation API に送信したテキストは Google 側ログに残ります。Google Cloud Console で Translation API 利用ログを確認・削除可能（顧客が自社 GCP プロジェクトを使う場合は顧客管轄）。

### 4.5 営業時間と SLA

| プラン | 削除依頼受付 → 完了までの目安 |
|---|---|
| Starter / Business | 5 営業日以内 |
| Enterprise | 2 営業日以内（SLA 契約条項に従う） |

---

## 5. データ保持期間

| データ種別 | 保持期間 | 自動削除 |
|---|---|---|
| 指示・応答ログ | **約 60 日** (`RETENTION_SECONDS = 60 * 24 * 60 * 60`) | 自動 (起動時 + 都度) |
| プレゼンス（接続中スタッフ） | 24 時間 | 自動 |
| 翻訳キャッシュ | 無期限（パフォーマンス目的、暗号化された業務指示の翻訳結果のみ） | なし |
| やさしい日本語キャッシュ | 同上 | なし |
| ワークスペースメタ・スタッフ情報 | 顧客が削除を依頼するまで | なし |
| ファイルアップロード（アバター・指示画像） | 顧客が削除を依頼するまで | なし |

---

## 6. アクセス制御 / 監査

### 6.1 認証

- 管理者: JWT (HS256) ベースのセッショントークン、24 時間有効
- スタッフ: 同上、ただし**個人スタッフアカウント必須**（共有パスワード方式は廃止）
- 総運営: 別 JWT、`super_admin_password` 認証、WebSocket 接続不可

### 6.2 監査ログ（拡張可能）

現状は指示・応答が DB に永続化されており、`誰がいつ何をスタッフに送ったか` `スタッフは何時に応答したか` を全件追跡可能。

エンタープライズ要件（管理者の操作監査・ログイン履歴・データアクセス監査等）が必要な場合は、別途監査テーブルを追加可能。Postgres の `pgaudit` 拡張も導入可。

### 6.3 IP 制限・MFA

現バージョンは IP 制限・多要素認証は未実装。エンタープライズ顧客向けに以下を追加可能:
- IP 許可リスト（管理者ログイン時）: 1 週間
- MFA (TOTP): 2〜3 週間

---

## 7. インシデント対応

| 種別 | 初動応答 |
|---|---|
| サービス停止（自社管轄） | 24 時間以内に対象顧客へ第 1 報、原因究明と復旧 |
| データ漏洩疑い | 即時調査、72 時間以内に顧客通知（GDPR 基準準拠） |
| Supabase / Render 等プラットフォーム障害 | プロバイダのステータスページ参照、影響顧客へ第 1 報 |

連絡先: PM 窓口（後述「体制」セクション）。

---

## 8. DPA・セキュリティチェックシート対応

以下の標準的書式に対応可能:

- 個人情報保護委員会の **個人情報の取扱状況の点検シート**
- **PMS / ISMS 体系のチェック項目**
- 顧客指定書式（NDA · 業務委託契約 · 個別 DPA · クラウドサービスチェックシート等）

雛形がある場合は提供をお願いいたします。標準的な質問は本ドキュメントの内容で回答可能、固有の質問は個別協議。

---

## 9. 既知の制約と今後の改善計画

| 項目 | 現状 | 改善計画 |
|---|---|---|
| Postgres RLS | アプリケーション層分離のみ | エンタープライズ顧客導入時に対応 |
| MFA | 未実装 | 2026 Q3 予定 |
| 監査ログ専用テーブル | 未実装 | エンタープライズ顧客導入時に対応 |
| ファイルストレージ | サーバディスク | Supabase Storage / S3 への移行計画あり |
| 削除操作の操作ログ | 30日保持（簡易） | エンタープライズで詳細監査ログへ拡張 |
| バックアップ | Supabase 標準（毎日自動） | Enterprise SLA で履歴 14 日 / 30 日に延長 |

---

## 10. 連絡先

技術問い合わせ・セキュリティチェックシート提出: PM 窓口へ（メール・電話）。
事業窓口: 営業窓口へ。
（後述「体制」セクションに具体的な連絡先記載）
