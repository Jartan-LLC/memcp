"""Query, right memory and near-miss wrong memory, in Chinese, Japanese, Korean and Thai.

The right memory must outrank the wrong one. The table is a regression guard written
by hand, not a benchmark: the cases are realistic, but no standard collection backs
them. `MISSES` are the pairs the scorer is known to get wrong, and why.
"""

from __future__ import annotations

# A number under three characters next to a Latin word is dropped by the floor in an
# ASCII query (Node 20), or rides on the word's prefix match (Python3 finds Python 2).
NUMBER = "a short number next to a Latin word doesn't tell versions apart"
# Pairs such as の資 are common to many memories; telling them apart needs weighting.
PARTICLE = "a particle only in the query adds pairs the right memory lacks"

CASES: list[tuple[str, str, str]] = [
    # Japanese, plain
    ("東京の天気", "明日の東京の天気は晴れ", "大阪の天気は雨"),
    ("会議室", "会議室は3階にある", "会議は午後から"),
    ("打ち合わせ", "打ち合わせは毎週月曜", "週次の振り返り"),
    ("ラーメン", "駅前のラーメン屋がおいしい", "駅前のカレー屋"),
    ("誕生日", "妻の誕生日は5月12日", "結婚記念日は6月"),
    ("締め切り", "レポートの締め切りは金曜", "レポートの提出先は田中さん"),
    ("好きな食べ物", "好きな食べ物は寿司", "嫌いな食べ物はピーマン"),
    # Japanese, latin long
    ("Dockerの設定", "Dockerの設定はcompose.yamlにある", "Kubernetesの設定はhelmで管理"),
    ("Pythonのバージョン", "Pythonのバージョンは3.12", "Nodeのバージョンは20"),
    ("GitHub Actions", "CIはGitHub Actionsで回している", "CIはJenkinsで回している"),
    ("Slackの通知", "Slackの通知はオフにしている", "メールの通知はオンにしている"),
    (
        "Reactコンポーネント",
        "Reactのコンポーネントはsrc/にある",
        "Vueのコンポーネントはsrc/にある",
    ),
    ("TypeScriptで書く", "フロントはTypeScriptで書く", "バックエンドはGoで書く"),
    ("VS Code 拡張", "VS Codeの拡張はvimを使う", "Emacsの拡張はevil"),
    # Japanese, latin short
    ("S3バケット", "S3バケットはus-east-1にある", "GCSバケットはasiaにある"),
    ("S3バケット", "S3のバケットはus-east-1にある", "GCSのバケットはasiaにある"),
    ("S3のバケット", "S3バケットはus-east-1にある", "GCSバケットはasiaにある"),
    ("AIの使い方", "AI の使い方を学ぶ", "Excelの使い方を学ぶ"),
    ("AI活用", "業務でのAI活用を進める", "業務でのIT活用を進める"),
    ("AI活用", "業務でAIを活用する", "業務でITを活用する"),
    ("AIの活用", "業務でのAI活用を進める", "業務でのIT活用を進める"),
    ("CIが失敗", "CI が失敗した原因はキャッシュ", "テストが失敗した"),
    ("CI失敗", "CIが失敗した原因はキャッシュ", "テストが失敗した"),
    ("CIの設定", "CI設定は.github/workflowsにある", "CD設定はargoにある"),
    ("DB設計", "DBの設計はERDにまとめた", "API設計はOpenAPIにまとめた"),
    ("DB設計", "DB設計はERDにまとめた", "API設計はOpenAPIにまとめた"),
    ("PR作成", "PRを作成したらレビュー依頼", "Issueを作成したらラベル付け"),
    ("PRレビュー", "PRレビューは2人以上", "コードレビューは2人以上"),
    ("PRのレビュー", "PRレビューは2人以上", "設計のレビューは2人以上"),
    ("UI改善", "UI 改善のタスク", "UX 改善のタスク"),
    ("UI/UX改善", "UI/UXの改善案を出す", "性能の改善案を出す"),
    ("UI/UX改善", "UX改善を優先する", "性能改善を優先する"),
    ("Go言語", "Go 言語の勉強", "Rust言語の勉強"),
    ("Goで書いた", "APIサーバーはGoで書いた", "APIサーバーはJavaで書いた"),
    ("PCの電源", "PCの電源が入らない", "スマホの電源が入らない"),
    ("QAチーム", "QAチームは大阪", "開発チームは東京"),
    ("ITの部署", "IT部署の担当は佐藤さん", "人事部署の担当は鈴木さん"),
    ("A社の件", "A社の件は来週", "B社の件は来週"),
    ("A社", "A社との契約は3月まで", "B社との契約は3月まで"),
    ("AWSのS3バケット", "S3バケットはAWSの東京リージョン", "GCSバケットはGCPの東京リージョン"),
    ("iOSアプリ", "iOSアプリはSwiftで書く", "Androidアプリはkotlinで書く"),
    ("iOSのアプリ", "iOSアプリはSwiftで書く", "Androidアプリはkotlinで書く"),
    ("Gitのブランチ", "Gitブランチはmain", "SVNのブランチはtrunk"),
    ("JSのテスト", "JSテストはjestで書く", "Pythonのテストはpytest"),
    # Japanese, number
    ("3月の会議", "3月の会議は中止", "5月の会議は中止"),
    ("3月", "3月に引っ越す", "5月に引っ越す"),
    ("3月", "3 月に引っ越す", "5 月に引っ越す"),
    ("3月に引っ越す", "3月に引っ越す予定", "5月に引っ越す予定"),
    ("2024年3月の売上", "2024年3月の売上は100万円", "2024年5月の売上は80万円"),
    ("2024年3月", "2024年の3月に入社", "2024年の5月に退社"),
    ("3月15日", "締め切りは3月15日", "締め切りは5月15日"),
    ("3/15", "締め切りは3/15", "締め切りは5/15"),
    ("15時", "会議は15時から", "会議は10時から"),
    ("15時から", "会議は15時から", "会議は10時から"),
    ("午後3時", "午後3時に歯医者", "午後5時に歯医者"),
    ("10時", "10時に集合", "3時に集合"),
    ("3階", "会議室は3階", "会議室は5階"),
    ("第3回", "第3回の勉強会", "第5回の勉強会"),
    ("3人", "チームは3人", "チームは5人"),
    ("100円", "コーヒーは100円", "紅茶は150円"),
    ("Python3系", "Python 3系のサポートを終了", "Python 2系のサポートを終了"),
    ("v2対応", "v2 対応済み", "v1 対応済み"),
    ("v2対応", "v2対応済み", "v1対応済み"),
    ("Node 20", "Node 20に上げた", "Node 18に上げた"),
    ("Node20に上げた", "Node 20に上げた", "Node 18に上げた"),
    ("1.2系", "1.2系に移行", "1.3系に移行"),
    ("v1.2系", "v1.2 系に移行した", "v1.3 系に移行した"),
    ("1-2月", "1-2月は繁忙期", "3-4月は繁忙期"),
    ("5G回線", "5G 回線は遅い", "4G 回線は遅い"),
    ("東京2024", "2024年の東京オリンピック", "2020年の東京オリンピック"),
    ("3年", "3年前に入社", "5年前に入社"),
    ("3年前", "3年前に入社", "3日前に入社"),
    ("2時間", "会議は2時間", "会議は3時間"),
    # Chinese, plain
    ("北京天气", "北京天气很冷", "上海天气很热"),
    ("会议室", "会议室在三楼", "会议在下午"),
    ("生日", "我妻子的生日是五月", "结婚纪念日是六月"),
    # Chinese, latin long
    ("用Python写测试", "我用 Python 写测试", "我用 Java 写测试"),
    ("Docker配置", "Docker 配置在 compose.yaml", "K8s 配置在 helm"),
    ("Python版本", "Python版本是3.12", "Node版本是20"),
    # Chinese, latin short
    ("用AI写代码", "我用 AI 写代码", "我用 IDE 写代码"),
    ("用AI写代码", "我用AI来写代码", "我用IDE来写代码"),
    ("AI写代码", "用 AI 写代码很方便", "用手写代码很累"),
    ("AI模型", "AI 模型部署在 GPU 上", "推荐模型部署在 CPU 上"),
    ("AI模型", "我们的AI大模型", "我们的推荐大模型"),
    ("用Go写", "用 Go 写的服务", "用 Java 写的服务"),
    ("5G网络", "5G 网络覆盖", "4G 网络覆盖"),
    ("S3存储桶", "S3 存储桶在 us-east-1", "OSS 存储桶在杭州"),
    ("S3的存储桶", "S3 存储桶在 us-east-1", "OSS 存储桶在杭州"),
    ("UI设计", "UI 设计由小王负责", "后端设计由小李负责"),
    ("UI设计", "UI的设计由小王负责", "后端的设计由小李负责"),
    ("CI配置", "CI 的配置在 .github", "CD 的配置在 argo"),
    ("A公司", "A公司的合同三月到期", "B公司的合同三月到期"),
    ("JS代码", "JS 代码在 src", "Go 代码在 cmd"),
    # Chinese, number
    ("3月", "3月搬家", "5月搬家"),
    ("3月份的会议", "3月份的会议取消了", "5月份的会议取消了"),
    ("2024年3月", "2024年3月入职", "2024年5月离职"),
    ("下午3点", "下午3点开会", "下午5点开会"),
    ("3点开会", "下午 3 点开会", "下午 5 点开会"),
    ("100元", "咖啡100元", "茶50元"),
    ("v2版本", "v2 版本已上线", "v1 版本已下线"),
    ("iPhone 15", "我用 iPhone 15", "我用 iPhone 14"),
    ("iPhone15", "我用iPhone 15 Pro", "我用iPhone 14 Pro"),
    ("三月", "三月搬家", "五月搬家"),
    # Korean, plain
    ("서울 맛집", "서울 맛집 목록", "부산 맛집 목록"),
    ("회의록", "회의록은 노션에 있다", "회의는 매주 월요일"),
    ("회의록 정리", "회의록을 정리했다", "메일을 정리했다"),
    ("생일", "아내 생일은 5월 12일", "결혼기념일은 6월"),
    # Korean, latin long
    ("Python으로", "Python을 주로 쓴다", "Java를 주로 쓴다"),
    ("Docker 설정", "Docker 설정은 compose 파일에", "Kubernetes 설정은 helm에"),
    # Korean, latin short
    ("AI로 요약", "AI가 요약해 준다", "사람이 요약해 준다"),
    ("AI 모델", "AI 모델은 GPU에서 돈다", "추천 모델은 CPU에서 돈다"),
    ("DB 설계", "DB 설계 문서", "API 설계 문서"),
    ("PR 리뷰", "PR 리뷰는 두 명 이상", "코드 리뷰는 두 명 이상"),
    ("A사", "A사와 계약", "B사와 계약"),
    ("UI를", "UI를 개선했다", "UX를 개선했다"),
    ("AI를 활용", "AI를 활용한 업무", "IT를 활용한 업무"),
    ("AI 활용", "AI를 활용한 업무", "IT를 활용한 업무"),
    ("AI활용", "AI 활용 사례", "IT 활용 사례"),
    ("JS 코드", "JS 코드는 src에", "Go 코드는 cmd에"),
    # Korean, number
    ("3월", "3월에 이사", "5월에 이사"),
    ("3월 회의", "3월 회의는 취소", "5월 회의는 취소"),
    ("3시", "오후 3시에 회의", "오후 5시에 회의"),
    ("오후 3시", "오후 3시에 회의", "오후 5시에 회의"),
    ("2024년 3월", "2024년 3월에 입사", "2024년 5월에 퇴사"),
    ("100원", "커피 100원", "차 50원"),
    ("v2 버전", "v2 버전 배포", "v1 버전 배포"),
    ("2시간", "회의는 2시간", "회의는 3시간"),
    # Thai, plain
    ("ร้านกาแฟ", "ร้านกาแฟใกล้บ้าน", "ร้านอาหารใกล้บ้าน"),
    ("ประชุม", "ประชุมทุกวันจันทร์", "สัมมนาทุกวันศุกร์"),
    ("กาแฟ", "ดื่มกาแฟทุกเช้า", "ดื่มชาทุกเช้า"),
    ("ภาษาไทย", "เรียนภาษาไทย", "เรียนภาษาจีน"),
    # Thai, latin short
    ("ใช้AI", "ใช้ AI ช่วยเขียน", "ใช้มือเขียน"),
    ("AIช่วย", "AI ช่วยเขียนโค้ด", "คนช่วยเขียนโค้ด"),
    # Thai, latin long
    ("Python", "เขียนด้วย Python", "เขียนด้วย Java"),
    # Thai, number
    ("3วัน", "ลา 3 วัน", "ลา 5 วัน"),
    ("ราคา 100 บาท", "ราคา 100 บาท", "ราคา 50 บาท"),
    ("100บาท", "ราคา 100 บาท", "ราคา 50 บาท"),
    ("3 โมง", "นัด 3 โมง", "นัด 5 โมง"),
    ("บ่าย3โมง", "นัดบ่าย 3 โมง", "นัดบ่าย 5 โมง"),
    # Japanese, shared char
    ("東京タワー", "東京タワーに登った", "京都タワーに登った"),
    ("京都", "京都で紅葉を見た", "東京で紅葉を見た"),
    ("大阪城", "大阪城の桜", "大阪の城下町"),
    # Chinese, shared char
    ("北京大学", "他在北京大学读书", "他在北京读大学"),
    ("上海", "上海的房价很高", "海上的风很大"),
    # Japanese, one char
    ("猫", "猫を飼っている", "犬を飼っている"),
    # Chinese, one char
    ("茶", "我喜欢喝茶", "我喜欢喝咖啡"),
    # Korean, one char
    ("밥", "밥을 먹었다", "빵을 먹었다"),
    # Japanese, order
    ("天気 東京", "東京の天気は晴れ", "大阪の天気は雨"),
    # Chinese, order
    ("天气 北京", "北京天气很冷", "上海天气很热"),
    # Korean, order
    ("날씨 서울", "서울의 날씨는 맑다", "부산의 날씨는 흐리다"),
    # Japanese, particle query
    ("会議の資料", "会議資料は共有フォルダ", "研修の資料は共有フォルダ"),
    ("妻の誕生日", "妻誕生日5月", "夫の誕生日は6月"),
    # Korean, particle query
    ("회의를 정리", "회의 정리 완료", "메일 정리 완료"),
    # English, with a Japanese name
    ("meeting in 東京", "東京での meeting は来週", "Osaka meeting next week"),
    ("dinner at 寿司屋", "寿司屋で dinner", "ramen shop dinner"),
    ("Tanaka-san 誕生日", "田中さんの誕生日 Tanaka-san", "佐藤さんの誕生日"),
    # Japanese, long run
    (
        "新しいプロジェクトの進め方",
        "新しいプロジェクトの進め方を決めた",
        "古いシステムの直し方を調べた",
    ),
    ("来週の打ち合わせの議題", "来週の打ち合わせの議題は予算", "今月の飲み会の場所は渋谷"),
    # Chinese, long run
    ("下周会议的议程", "下周会议的议程是预算", "这个月聚餐的地点"),
    # Japanese, katakana
    ("コーヒー", "毎朝コーヒーを飲む", "毎朝コーラを飲む"),
    ("スケジュール", "スケジュールを更新", "スクリーンを更新"),
    # Japanese, kanji kana
    ("引っ越し", "来月引っ越しする", "来月引き出しを買う"),
    # Japanese, Latin word alone
    ("React", "フロントはReactで書く", "フロントはVueで書く"),
    ("linter", "linterはruffを使う", "formatterはblackを使う"),
    ("linting", "linterはruffを使う", "formatterはblackを使う"),
]

MISSES: dict[tuple[str, str], str] = {
    ("Python3系", "Python 3系のサポートを終了"): NUMBER,
    ("Node 20", "Node 20に上げた"): NUMBER,
    ("Node20に上げた", "Node 20に上げた"): NUMBER,
    ("iPhone 15", "我用 iPhone 15"): NUMBER,
    ("iPhone15", "我用iPhone 15 Pro"): NUMBER,
    ("会議の資料", "会議資料は共有フォルダ"): PARTICLE,
    ("妻の誕生日", "妻誕生日5月"): PARTICLE,
}
