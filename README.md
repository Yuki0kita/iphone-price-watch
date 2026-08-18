# iPhone 買取価格ウォッチ

買取1丁目のiPhone未開封買取価格を1時間ごとに記録し、高値になったら気づけるようにする個人用ツール。
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

買取1丁目の商品ページはVite製のSPAで、サーバーが返すHTMLに価格が入っていない。
そのため画面自体が使っている公開JSON APIを、同じ形で1商品につき1回だけ呼ぶ。

```text
GET https://www.1-chome.com/api/keitai/getKeitaiItem?keitaiItemId=<id>&keitaiItemKbId=<kbId>
```

`id` と `kbId` は商品ページURL（`/productDetail/1371/1927`）から自動で取り出すため、
設定に書くのは商品ページのURLだけでよい。

取得するのは `keitaiKbDetails` の中の「未開封」の価格。同じ商品に並ぶ「開封済未使用品」は対象外。

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

- 目標価格（`target_price`）以上になった
- 前回の記録から3,000円以上上がった
- 直近30日の高値を更新した（記録が7点たまってから判定）

同じ価格での連打を防ぐため、一度通知したら12時間は同じ商品を通知しない。

## 設定を変える

`config/products.json` を編集する。

```json
{
  "id": "iphone-17-pro-max-512",
  "name": "iPhone 17 Pro Max 512GB",
  "url": "https://www.1-chome.com/productDetail/1371/1927",
  "target_price": 240000,
  "enabled": true
}
```

- `target_price`: この金額以上で通知する。`null` なら目標価格の判定をしない
- `enabled`: `false` にすると取得を止める（履歴は残る）

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
- 1機種の取得に失敗しても他は続行する。全機種が失敗した時だけワークフローを失敗扱いにする
- サイト側の仕様が変わると「未開封の価格が見つからない」というエラーで止まる。
  ダッシュボード下部にエラー件数が出るので、そこで気づける
