# 買取価格ウォッチ

買取1丁目の買取価格を1時間ごとに記録し、高値になったら気づけるようにする個人用ツール。
iPhoneのほか、ゲーム機・カメラ・トレカBOXを監視する。
GitHub Actions と GitHub Pages だけで動くため、月額0円で運用できる。

- ダッシュボード: `docs/index.html`（GitHub Pagesで公開）
- 価格履歴: `docs/data/history.json`
- 監視設定: `config/products.json`

## 仕組み

```text
買取1丁目のJSON API
   ↓ 毎時17分
GitHub Actions（src/collector.py）
   ↓
docs/data/history.json に追記してコミット
   ├─→ GitHub Pages（30日グラフ）
   └─→ 通知条件に一致したらGmailで送信
```

毎時00分付近はGitHub Actionsが混みやすく遅延するため、17分にずらしている。

### 価格の取得元

買取1丁目のページはVite製のSPAで、サーバーが返すHTMLに価格が入っていない。
そのため画面自体が使っている公開JSON APIを同じ形で呼ぶ。

商品の種類でAPIも状態名も違うため、設定の `source` で切り替える。

| source | API | 対象 | 状態名の例 |
|---|---|---|---|
| `keitai` | `/api/keitai/getKeitaiItem` | iPhone等の携帯 | 未開封 |
| `goods` | `/api/goods/listPage` | 家電・ゲーム・カメラ・トレカ | 新品未使用 / シュリンク有 / 印(購入店シール)なし |
| `market` | （買取1丁目は使わない） | 買取1丁目が扱わない商品 | — |

`keitai` は商品ページURL（`/productDetail/1371/1927`）からIDを自動で取り出す。

`goods` は一覧APIを `cate_code` と `keyword` で絞り、`jan` が一致する商品を選ぶ。
一覧を全件取ると重いため、`keyword` で必ず絞る。

`condition` には追跡したい状態名を**正確に**書く。状態名はカテゴリごとに違い、
指定が間違っていると「取得できた状態」の一覧つきでエラーになる。

`market` は買取1丁目に商品が無い場合に使う。買取X（後述）経由でのみ記録する。

## 他店との比較（任意）

買取1丁目だけを見ても「その店の価格」しか分からない。実際に受け取れる額は一番高く買う店で決まるため、
[買取X](https://kaitorix.app/) のOpen APIで複数店舗を横断した最高買取価格を1日1回だけ取得する。

```text
GET https://kaitorix.app/open/api/product/<JAN>
Authorization: Bearer ktx_...
```

無料プランは30リクエスト/日・1リクエスト/秒。現在の監視11商品なら1日11回で収まる。

APIキーは https://kaitorix.app/open/mypage で取得し、GitHub Secretsに `KTX_API_KEY` として登録する。
未登録なら他店比較だけがスキップされ、買取1丁目の記録は通常どおり動く。

疎通確認はJANを指定して実行する。

```bash
KTX_API_KEY=ktx_xxx python3 -m src.kaitorix 4549995649154
```

JANは色ごとに違う。`config/products.json` の `jan` には、自分が持っている色のJANを入れる。

買取Xの利用規約上、個人の判断用途は想定内だが、事業目的の継続利用やデータの再配布は事前申込が必要。

## セットアップ

### 1. GitHub Pagesを有効にする

`Settings → Pages → Source: Deploy from a branch → Branch: main / docs`

数十秒でダッシュボードが公開される。

### 2. メール通知を設定する（任意）

`Settings → Secrets and variables → Actions` に3つ登録する。

```text
SMTP_USERNAME       Gmailアドレス
SMTP_APP_PASSWORD   Googleの「アプリパスワード」
NOTIFY_TO           通知を受け取るアドレス
```

`SMTP_APP_PASSWORD` はGoogleアカウントのログインパスワードではない。
2段階認証を有効にしたうえで「アプリパスワード」を発行して使う。

未設定でも価格の記録は動く。通知だけがスキップされる。

## 通知条件

次のいずれかに当てはまると通知する。

- 目標価格（`target_price`）以上になった（買取1丁目と他店のうち高いほうで判定）
- 前回の記録から3,000円以上上がった
- 直近30日の高値を更新した（記録が7点たまってから判定）
- 他店が買取1丁目より5,000円以上高い（`market_gap_yen`、APIキー登録時のみ）

同じ価格での連打を防ぐため、一度通知したら12時間は同じ商品を通知しない。

## 設定を変える

`config/products.json` を編集する。

携帯（source省略時）:

```json
{
  "id": "iphone-17-256",
  "name": "iPhone 17 256GB",
  "url": "https://www.1-chome.com/productDetail/1365/1909",
  "jan": "4549995649154",
  "target_price": 139800,
  "enabled": true
}
```

家電・トレカ:

```json
{
  "id": "ps5-pro-cfi-7000b01",
  "name": "PlayStation 5 Pro CFI-7000B01",
  "source": "goods",
  "cate_code": "20480828",
  "keyword": "CFI-7000B01",
  "jan": "4948872416320",
  "condition": "新品未使用",
  "url": "https://www.1-chome.com/electricAppliance",
  "target_price": null,
  "enabled": true
}
```

- `target_price`: この金額以上で通知する。`null` なら目標価格の判定をしない
- `enabled`: `false` にすると取得を止める（履歴は残る）
- `jan`: 買取Xで他店と比較するために使う。色ごとに違う点に注意

しきい値は `settings.alert` で変えられる。

## 監視対象を追加する

iPhoneカテゴリの全機種と現在の未開封価格を一覧できる。

```bash
python3 -m src.discover
```

```bash
python3 -m src.discover --keyword "17 Pro" --json
```

`--json` を付けると `config/products.json` の `products` にそのまま貼れる形で出力する。

## ローカルで動かす

外部ライブラリは使っていない。Python 3.9以上があればそのまま動く。

```bash
python3 -m unittest discover -s tests -t .
```

```bash
python3 -m src.collector
```

```bash
python3 -m http.server 8657 --directory docs
```

## 運用メモ

- 価格が変わらない日は1日1件だけ記録する。履歴が無駄に増えないようにするため
- 履歴は180日で切り捨てる（`settings.history_days`）
- 1商品の取得に失敗しても他は続行する。全商品が失敗した時だけワークフローを失敗扱いにする
- サイト側の仕様や状態名が変わると「指定した状態の価格が見つからない」というエラーになる。
  エラーには実際に取得できた状態名が並ぶので、`condition` を直せば復旧する。
  ダッシュボード下部にエラー件数が出るので、そこで気づける
