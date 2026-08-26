/* Stake Code Claimer — Mini App (vanilla JS, no framework).
 * Auth: Telegram initData -> POST /auth -> session JWT in MEMORY ONLY.
 * Tabs: Accounts · Buy · Manage · Stats · Drop. Dark, professional.
 * CSP: no inline <style>/style= attributes — styling via classes (+ el.style CSSOM).
 */
(function () {
  "use strict";

  var API = "/app/api/v1";
  var tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

  var token = null, isAdmin = false, refreshTimer = null, current = "accounts", renderSeq = 0;
  var expiryTick = null;   // singleton interval that live-refreshes expiry countdowns
  var LANGS = [
    ["en", "🇬🇧 English"], ["ja", "🇯🇵 日本語"], ["zh", "🇨🇳 中文"], ["ko", "🇰🇷 한국어"],
    ["hi", "🇮🇳 हिन्दी"], ["pl", "🇵🇱 Polski"], ["vi", "🇻🇳 Tiếng Việt"], ["es", "🇪🇸 Español"],
    ["it", "🇮🇹 Italiano"], ["pt", "🇧🇷 Português"], ["fr", "🇫🇷 Français"], ["tr", "🇹🇷 Türkçe"]
  ];

  // ── i18n (Mini App is localized independently of the bot) ────────────────
  // English is the complete source; t() falls back to English for any missing
  // key, so partially-translated languages still render. High-visibility strings
  // (nav + section titles) are translated for all 12; add more keys any time.
  var MSG = {
    en: { nav_accounts: "Accounts", nav_buy: "Buy", nav_manage: "Manage", nav_stats: "Stats", nav_drop: "Drop", language: "Language", acc_subs: "My Subscriptions", buy_title: "Buy a Slot", manage_title: "Manage your slots", stats_title: "Your Claims", drop_title: "Drop a code", dur_1day: "1 Day (24h)", dur_days: "Days", dur_day: "day", plan_soon: "Soon", plan_pricing_soon: "Pricing soon", buy_choose_plan: "Choose a plan", buy_plan_note: "Your subscription activates the moment your payment is confirmed, and runs for the plan's duration.", acc_pool: "SLOT POOL AVAILABILITY", slots_available: "slots available", acc_none_t: "No subscriptions yet", acc_none_s: "Automate your Stake claims with one of our plans.", getstarted: "Get Started", st_expired: "Expired", time_left: "left", no_expiry: "No expiry", expires_prefix: "Expires", st_online: "Online", st_offline: "Offline", st_active: "Active", err_dashboard: "Could not load your dashboard.", err_plans: "Could not load plans.", err_slots: "Could not load your slots.", err_stats: "Could not load stats.", err_generic: "Something went wrong", buy_step1: "Your Stake API key", buy_key_ph: "Paste your Stake API key", buy_verify: "Verify key", verify_enter_key: "Enter your API key first.", verifying: "Verifying…", verified: "Verified", scroll_cue: "Scroll down to choose your plan", verify_unavail: "Verification temporarily unavailable — please try again shortly.", verify_invalid: "Invalid API key — please check and try again.", verify_failed: "Verification failed — try again.", buy_step3: "Configure this slot", buy_pay: "Continue to payment", cfg_wc: "Withdrawal currency", cfg_rc: "Reload currency", cfg_min: "Minimum value to claim (0 = all)", cfg_av: "Auto Vault", cfg_ab: "Auto Bonus", cfg_ar: "Auto Reload", pay_need: "Verify a key and pick a plan first", creating_order: "Creating order…", already_active: "Already active", no_slots_now: "No slots available right now", pay_failed: "Could not start payment", pay_open_btn: "Open payment page", pay_copy: "Copy link", pay_copied: "Link copied", pay_link_hint: "If the payment page didn't open, tap the button or copy the link below.", pay_no_url: "Couldn't get a payment link — please try again.", pay_back: "Back", wait_pay_t: "Waiting for payment…", wait_pay_s: "Complete the payment in the window that opened. This screen updates automatically.", sub_active_t: "Subscription active!", sub_active_s: "is now claiming.", your_slot: "Your slot", view_subs: "View my subscriptions", pay_not_done: "Payment not completed", mng_none_t: "No slots to manage", mng_none_s: "Buy a slot first, then configure it here.", slot_expired_toast: "This slot has expired", slot_word: "Slot", mng_replace_key: "Replace API key (optional — re-verified)", mng_replace_ph: "Leave blank to keep current", save_changes: "Save changes", saving: "Saving…", saved: "Saved", mng_verify_fail: "New key could not be verified", save_failed: "Save failed", no_earnings: "Nothing earned yet", win_24h: "Today", win_7d: "Last 7 days", win_30d: "Last 30 days", type_all: "All", type_drop: "Drops", type_reload: "Reloads", claimed: "Claimed", total_earned: "TOTAL EARNED", successful_claims: "codes claimed", recent_trunc: "Showing latest 50", recent_codes: "Recent claims (last 7 days)", no_recent: "No claims in the last 7 days.", warn_slow: "⚠️ You're going very fast — please wait a few seconds, otherwise you'll be paused for a bit.", reload_unavail: "⚠ Reload unavailable currently", reload_next: "Next reload:", reload_now: "available now", drop_note: "This code is sent <b>ONLY to your own Stake API keys</b> (your slots) — it is never shared with anyone else.", drop_code: "Code", drop_code_ph: "e.g. abc123", drop_hint1: "A <b>drop</b> is a normal Stake bonus-drop code. We instantly claim it on every one of your slots.", drop_bonus_label: "This is a bonus code", drop_hint2: "Turn this on <b>only</b> for weekly / monthly stream &amp; bonus-reward codes — they are claimed through Stake’s bonus flow instead of the drop flow.", drop_btn: "Drop to my slots", enter_code: "Enter a code", dropping: "Dropping…", dropped_to: "Sent to", no_active_slots: "No active slots to drop to", invalid_code: "Invalid code", drop_failed: "Drop failed", lang_set: "Language set" },
    ja: { nav_accounts: "アカウント", nav_buy: "購入", nav_manage: "管理", nav_stats: "統計", nav_drop: "ドロップ", language: "言語", acc_subs: "マイサブスク", buy_title: "スロット購入", manage_title: "スロット管理", stats_title: "クレーム統計", drop_title: "コードをドロップ", dur_1day: "1日 (24時間)", dur_days: "日", dur_day: "日", plan_soon: "近日", plan_pricing_soon: "価格は近日", buy_choose_plan: "プランを選択", buy_plan_note: "支払い確認後すぐに有効になり、プラン期間中ご利用いただけます。", acc_pool: "スロット空き状況", slots_available: "スロット利用可能", acc_none_t: "まだサブスクなし", acc_none_s: "プランでStakeの受取を自動化しましょう。", getstarted: "始める", st_expired: "期限切れ", recent_trunc: "最新50件を表示", warn_slow: "⚠️ 操作が速すぎます。数秒お待ちください。続けると一時的に停止されます。", reload_unavail: "⚠ 現在リロードは利用できません", reload_next: "次のリロード:", reload_now: "今すぐ利用可能", pay_open_btn: "支払いページを開く", pay_copy: "リンクをコピー", pay_copied: "リンクをコピーしました", pay_link_hint: "支払いページが開かない場合は、ボタンをタップするか下のリンクをコピーしてください。", pay_no_url: "支払いリンクを取得できませんでした。もう一度お試しください。", pay_back: "戻る", time_left: "残り", no_expiry: "有効期限なし", expires_prefix: "有効期限", st_online: "オンライン", st_offline: "オフライン", st_active: "有効", err_dashboard: "ダッシュボードを読み込めませんでした。", err_plans: "プランを読み込めませんでした。", err_slots: "スロットを読み込めませんでした。", err_stats: "統計を読み込めませんでした。", err_generic: "問題が発生しました", buy_step1: "StakeのAPIキー", buy_key_ph: "StakeのAPIキーを貼り付け", buy_verify: "キーを確認", verify_enter_key: "先にAPIキーを入力してください。", verifying: "確認中…", verified: "確認済み", scroll_cue: "下にスクロールしてプランを選択", verify_unavail: "確認が一時的に利用できません。しばらくして再試行してください。", verify_invalid: "APIキーが無効です。確認して再試行してください。", verify_failed: "確認に失敗しました。再試行してください。", buy_step3: "このスロットを設定", buy_pay: "支払いへ進む", cfg_wc: "出金通貨", cfg_rc: "リロード通貨", cfg_min: "受取の最小額 (0 = すべて)", cfg_av: "自動ボールト", cfg_ab: "自動ボーナス", cfg_ar: "自動リロード", pay_need: "先にキーを確認してプランを選択", creating_order: "注文作成中…", already_active: "すでに有効", no_slots_now: "現在利用可能なスロットがありません", pay_failed: "支払いを開始できませんでした", wait_pay_t: "支払いを待っています…", wait_pay_s: "開いたウィンドウで支払いを完了してください。この画面は自動的に更新されます。", sub_active_t: "サブスクが有効になりました！", sub_active_s: "が受取中です。", your_slot: "あなたのスロット", view_subs: "サブスクを表示", pay_not_done: "支払いが完了していません", mng_none_t: "管理するスロットがありません", mng_none_s: "まずスロットを購入し、ここで設定してください。", slot_expired_toast: "このスロットは期限切れです", slot_word: "スロット", mng_replace_key: "APIキーを変更 (任意 — 再確認)", mng_replace_ph: "現在のままにする場合は空欄", save_changes: "変更を保存", saving: "保存中…", saved: "保存しました", mng_verify_fail: "新しいキーを確認できませんでした", save_failed: "保存に失敗", no_earnings: "まだ収益なし", win_24h: "24時間 / 今日", win_7d: "7日", win_30d: "30日", type_all: "すべて", type_drop: "ドロップ", type_reload: "リロード", claimed: "受取済み", total_earned: "合計獲得", successful_claims: "成功した受取", recent_codes: "最近のコード (7日間)", no_recent: "過去7日間コードなし。", drop_note: "このコードは<b>あなた自身のStake APIキー</b>（あなたのスロット）にのみ送信され、他人と共有されることはありません。", drop_code: "コード", drop_code_ph: "例: abc123", drop_hint1: "<b>ドロップ</b>は通常のStakeボーナスドロップコードです。すべてのスロットで即座に受け取ります。", drop_bonus_label: "これはボーナスコードです", drop_hint2: "週次/月次のストリーム＆ボーナス報酬コードの場合<b>のみ</b>オンにしてください。ドロップではなくボーナスフローで受け取ります。", drop_btn: "スロットにドロップ", enter_code: "コードを入力", dropping: "ドロップ中…", dropped_to: "送信先", no_active_slots: "ドロップ先の有効なスロットがありません", invalid_code: "無効なコード", drop_failed: "ドロップ失敗", lang_set: "言語を設定しました" },
    zh: { nav_accounts: "账户", nav_buy: "购买", nav_manage: "管理", nav_stats: "统计", nav_drop: "掉落", language: "语言", acc_subs: "我的订阅", buy_title: "购买槽位", manage_title: "管理槽位", stats_title: "领取面板", drop_title: "投放代码", dur_1day: "1 天 (24小时)", dur_days: "天", dur_day: "天", plan_soon: "即将", plan_pricing_soon: "定价即将", buy_choose_plan: "选择套餐", buy_plan_note: "付款确认后订阅立即生效，并持续套餐时长。", acc_pool: "槽位可用性", slots_available: "个可用槽位", acc_none_t: "还没有订阅", acc_none_s: "用我们的套餐自动领取 Stake 奖励。", getstarted: "开始", st_expired: "已过期", recent_trunc: "显示最近50条", warn_slow: "⚠️ 操作过快，请等待几秒，否则将被暂时暂停。", reload_unavail: "⚠ 当前无法重载", reload_next: "下次重载:", reload_now: "现在可用", pay_open_btn: "打开支付页面", pay_copy: "复制链接", pay_copied: "链接已复制", pay_link_hint: "如果支付页面未打开，请点击按钮或复制下面的链接。", pay_no_url: "无法获取支付链接，请重试。", pay_back: "返回", time_left: "剩余", no_expiry: "无到期", expires_prefix: "到期", st_online: "在线", st_offline: "离线", st_active: "有效", err_dashboard: "无法加载仪表板。", err_plans: "无法加载套餐。", err_slots: "无法加载槽位。", err_stats: "无法加载统计。", err_generic: "出错了", buy_step1: "你的 Stake API 密钥", buy_key_ph: "粘贴你的 Stake API 密钥", buy_verify: "验证密钥", verify_enter_key: "请先输入 API 密钥。", verifying: "验证中…", verified: "已验证", scroll_cue: "向下滚动选择套餐", verify_unavail: "验证暂时不可用，请稍后再试。", verify_invalid: "API 密钥无效，请检查后重试。", verify_failed: "验证失败，请重试。", buy_step3: "配置此槽位", buy_pay: "继续付款", cfg_wc: "提现币种", cfg_rc: "返利币种", cfg_min: "领取的最小金额 (0 = 全部)", cfg_av: "自动金库", cfg_ab: "自动奖励", cfg_ar: "自动返利", pay_need: "请先验证密钥并选择套餐", creating_order: "创建订单中…", already_active: "已激活", no_slots_now: "当前没有可用槽位", pay_failed: "无法开始付款", wait_pay_t: "等待付款中…", wait_pay_s: "请在打开的窗口中完成付款。此界面会自动更新。", sub_active_t: "订阅已激活！", sub_active_s: "正在领取。", your_slot: "你的槽位", view_subs: "查看我的订阅", pay_not_done: "付款未完成", mng_none_t: "没有可管理的槽位", mng_none_s: "先购买槽位，然后在这里配置。", slot_expired_toast: "此槽位已过期", slot_word: "槽位", mng_replace_key: "替换 API 密钥 (可选 — 会重新验证)", mng_replace_ph: "留空以保留当前", save_changes: "保存更改", saving: "保存中…", saved: "已保存", mng_verify_fail: "无法验证新密钥", save_failed: "保存失败", no_earnings: "还没有收益", win_24h: "24小时 / 今天", win_7d: "7 天", win_30d: "30 天", type_all: "全部类型", type_drop: "掉落", type_reload: "返利", claimed: "已领取", total_earned: "总收益", successful_claims: "次成功领取", recent_codes: "最近的代码 (最近7天)", no_recent: "最近7天没有代码。", drop_note: "此代码<b>仅发送到你自己的 Stake API 密钥</b>（你的槽位）——绝不与他人共享。", drop_code: "代码", drop_code_ph: "例如 abc123", drop_hint1: "<b>drop</b> 是普通的 Stake 奖励掉落代码。我们会立即在你的每个槽位领取。", drop_bonus_label: "这是奖励代码", drop_hint2: "仅在每周/每月直播和奖励代码时<b>才</b>开启——它们通过 Stake 的奖励流程领取，而非掉落流程。", drop_btn: "投放到我的槽位", enter_code: "请输入代码", dropping: "投放中…", dropped_to: "已发送到", no_active_slots: "没有可投放的活动槽位", invalid_code: "无效代码", drop_failed: "投放失败", lang_set: "语言已设置" },
    ko: { nav_accounts: "계정", nav_buy: "구매", nav_manage: "관리", nav_stats: "통계", nav_drop: "드롭", language: "언어", acc_subs: "내 구독", buy_title: "슬롯 구매", manage_title: "슬롯 관리", stats_title: "클레임 대시보드", drop_title: "코드 드롭", dur_1day: "1일 (24시간)", dur_days: "일", dur_day: "일", plan_soon: "곧", plan_pricing_soon: "가격 곧", buy_choose_plan: "플랜 선택", buy_plan_note: "결제가 확인되는 즉시 구독이 활성화되며 플랜 기간 동안 유지됩니다.", acc_pool: "슬롯 가용성", slots_available: "슬롯 사용 가능", acc_none_t: "아직 구독 없음", acc_none_s: "플랜으로 Stake 클레임을 자동화하세요.", getstarted: "시작하기", st_expired: "만료됨", recent_trunc: "최근 50개 표시", warn_slow: "⚠️ 너무 빠릅니다. 몇 초 기다려 주세요. 계속하면 잠시 중지됩니다.", reload_unavail: "⚠ 현재 리로드 불가", reload_next: "다음 리로드:", reload_now: "지금 가능", pay_open_btn: "결제 페이지 열기", pay_copy: "링크 복사", pay_copied: "링크가 복사되었습니다", pay_link_hint: "결제 페이지가 열리지 않으면 버튼을 누르거나 아래 링크를 복사하세요.", pay_no_url: "결제 링크를 가져올 수 없습니다. 다시 시도해 주세요.", pay_back: "뒤로", time_left: "남음", no_expiry: "만료 없음", expires_prefix: "만료", st_online: "온라인", st_offline: "오프라인", st_active: "활성", err_dashboard: "대시보드를 불러올 수 없습니다.", err_plans: "플랜을 불러올 수 없습니다.", err_slots: "슬롯을 불러올 수 없습니다.", err_stats: "통계를 불러올 수 없습니다.", err_generic: "문제가 발생했습니다", buy_step1: "Stake API 키", buy_key_ph: "Stake API 키 붙여넣기", buy_verify: "키 확인", verify_enter_key: "API 키를 먼저 입력하세요.", verifying: "확인 중…", verified: "확인됨", scroll_cue: "아래로 스크롤하여 플랜 선택", verify_unavail: "확인을 일시적으로 사용할 수 없습니다. 잠시 후 다시 시도하세요.", verify_invalid: "API 키가 잘못되었습니다. 확인 후 다시 시도하세요.", verify_failed: "확인 실패 — 다시 시도하세요.", buy_step3: "이 슬롯 구성", buy_pay: "결제로 계속", cfg_wc: "출금 통화", cfg_rc: "리로드 통화", cfg_min: "클레임 최소 금액 (0 = 전체)", cfg_av: "자동 볼트", cfg_ab: "자동 보너스", cfg_ar: "자동 리로드", pay_need: "키를 확인하고 플랜을 먼저 선택하세요", creating_order: "주문 생성 중…", already_active: "이미 활성", no_slots_now: "현재 사용 가능한 슬롯 없음", pay_failed: "결제를 시작할 수 없습니다", wait_pay_t: "결제 대기 중…", wait_pay_s: "열린 창에서 결제를 완료하세요. 이 화면은 자동으로 업데이트됩니다.", sub_active_t: "구독 활성화됨!", sub_active_s: "이(가) 클레임 중입니다.", your_slot: "내 슬롯", view_subs: "내 구독 보기", pay_not_done: "결제가 완료되지 않음", mng_none_t: "관리할 슬롯 없음", mng_none_s: "먼저 슬롯을 구매한 후 여기서 구성하세요.", slot_expired_toast: "이 슬롯은 만료되었습니다", slot_word: "슬롯", mng_replace_key: "API 키 교체 (선택 — 재확인)", mng_replace_ph: "현재 유지하려면 비워 두세요", save_changes: "변경 사항 저장", saving: "저장 중…", saved: "저장됨", mng_verify_fail: "새 키를 확인할 수 없습니다", save_failed: "저장 실패", no_earnings: "아직 수익 없음", win_24h: "24시간 / 오늘", win_7d: "7일", win_30d: "30일", type_all: "전체", type_drop: "드롭", type_reload: "리로드", claimed: "클레임됨", total_earned: "총 수익", successful_claims: "성공한 클레임", recent_codes: "최근 코드 (최근 7일)", no_recent: "최근 7일간 코드 없음.", drop_note: "이 코드는 <b>본인의 Stake API 키</b>(내 슬롯)에만 전송되며 다른 사람과 공유되지 않습니다.", drop_code: "코드", drop_code_ph: "예: abc123", drop_hint1: "<b>드롭</b>은 일반 Stake 보너스 드롭 코드입니다. 모든 슬롯에서 즉시 클레임합니다.", drop_bonus_label: "보너스 코드입니다", drop_hint2: "주간/월간 스트림 및 보너스 보상 코드에만 <b>켜세요</b> — 드롭이 아닌 Stake 보너스 방식으로 클레임됩니다.", drop_btn: "내 슬롯에 드롭", enter_code: "코드를 입력하세요", dropping: "드롭 중…", dropped_to: "전송됨", no_active_slots: "드롭할 활성 슬롯 없음", invalid_code: "잘못된 코드", drop_failed: "드롭 실패", lang_set: "언어 설정됨" },
    hi: { nav_accounts: "खाते", nav_buy: "खरीदें", nav_manage: "प्रबंधन", nav_stats: "आँकड़े", nav_drop: "ड्रॉप", language: "भाषा", acc_subs: "मेरी सदस्यताएँ", buy_title: "स्लॉट खरीदें", manage_title: "स्लॉट प्रबंधन", stats_title: "क्लेम डैशबोर्ड", drop_title: "कोड ड्रॉप करें", dur_1day: "1 दिन (24घं)", dur_days: "दिन", dur_day: "दिन", plan_soon: "जल्द", plan_pricing_soon: "मूल्य जल्द", buy_choose_plan: "प्लान चुनें", buy_plan_note: "भुगतान की पुष्टि होते ही आपकी सदस्यता सक्रिय हो जाती है और प्लान अवधि तक चलती है।", acc_pool: "स्लॉट उपलब्धता", slots_available: "स्लॉट उपलब्ध", acc_none_t: "अभी कोई सदस्यता नहीं", acc_none_s: "हमारे प्लान से Stake क्लेम स्वचालित करें।", getstarted: "शुरू करें", st_expired: "समाप्त", recent_trunc: "नवीनतम 50 दिखा रहे हैं", warn_slow: "⚠️ आप बहुत तेज़ चल रहे हैं — कृपया कुछ सेकंड रुकें, वरना थोड़ी देर के लिए रोक दिया जाएगा।", reload_unavail: "⚠ रीलोड अभी उपलब्ध नहीं", reload_next: "अगला रीलोड:", reload_now: "अभी उपलब्ध", pay_open_btn: "भुगतान पेज खोलें", pay_copy: "लिंक कॉपी करें", pay_copied: "लिंक कॉपी हो गया", pay_link_hint: "यदि भुगतान पेज नहीं खुला, तो बटन दबाएँ या नीचे दिया गया लिंक कॉपी करें।", pay_no_url: "भुगतान लिंक नहीं मिल सका — कृपया पुनः प्रयास करें।", pay_back: "वापस", time_left: "शेष", no_expiry: "कोई समाप्ति नहीं", expires_prefix: "समाप्ति", st_online: "ऑनलाइन", st_offline: "ऑफ़लाइन", st_active: "सक्रिय", err_dashboard: "डैशबोर्ड लोड नहीं हो सका।", err_plans: "प्लान लोड नहीं हो सके।", err_slots: "स्लॉट लोड नहीं हो सके।", err_stats: "आँकड़े लोड नहीं हो सके।", err_generic: "कुछ गड़बड़ हो गई", buy_step1: "आपकी Stake API कुंजी", buy_key_ph: "अपनी Stake API कुंजी पेस्ट करें", buy_verify: "कुंजी सत्यापित करें", verify_enter_key: "पहले अपनी API कुंजी दर्ज करें।", verifying: "सत्यापन हो रहा है…", verified: "सत्यापित", scroll_cue: "प्लान चुनने के लिए नीचे स्क्रॉल करें", verify_unavail: "सत्यापन अस्थायी रूप से अनुपलब्ध — थोड़ी देर बाद पुनः प्रयास करें।", verify_invalid: "अमान्य API कुंजी — जाँचें और पुनः प्रयास करें।", verify_failed: "सत्यापन विफल — पुनः प्रयास करें।", buy_step3: "इस स्लॉट को कॉन्फ़िगर करें", buy_pay: "भुगतान जारी रखें", cfg_wc: "निकासी मुद्रा", cfg_rc: "रीलोड मुद्रा", cfg_min: "क्लेम का न्यूनतम मूल्य (0 = सभी)", cfg_av: "ऑटो वॉल्ट", cfg_ab: "ऑटो बोनस", cfg_ar: "ऑटो रीलोड", pay_need: "पहले कुंजी सत्यापित करें और प्लान चुनें", creating_order: "ऑर्डर बन रहा है…", already_active: "पहले से सक्रिय", no_slots_now: "अभी कोई स्लॉट उपलब्ध नहीं", pay_failed: "भुगतान शुरू नहीं हो सका", wait_pay_t: "भुगतान की प्रतीक्षा…", wait_pay_s: "खुली विंडो में भुगतान पूरा करें। यह स्क्रीन स्वतः अपडेट होगी।", sub_active_t: "सदस्यता सक्रिय!", sub_active_s: "अब क्लेम कर रहा है।", your_slot: "आपका स्लॉट", view_subs: "मेरी सदस्यताएँ देखें", pay_not_done: "भुगतान पूरा नहीं हुआ", mng_none_t: "प्रबंधित करने के लिए कोई स्लॉट नहीं", mng_none_s: "पहले स्लॉट खरीदें, फिर यहाँ कॉन्फ़िगर करें।", slot_expired_toast: "यह स्लॉट समाप्त हो गया", slot_word: "स्लॉट", mng_replace_key: "API कुंजी बदलें (वैकल्पिक — पुनः सत्यापित)", mng_replace_ph: "वर्तमान रखने के लिए खाली छोड़ें", save_changes: "परिवर्तन सहेजें", saving: "सहेजा जा रहा है…", saved: "सहेजा गया", mng_verify_fail: "नई कुंजी सत्यापित नहीं हो सकी", save_failed: "सहेजना विफल", no_earnings: "अभी कोई कमाई नहीं", win_24h: "24घं / आज", win_7d: "7 दिन", win_30d: "30 दिन", type_all: "सभी प्रकार", type_drop: "ड्रॉप", type_reload: "रीलोड", claimed: "क्लेम किया", total_earned: "कुल कमाई", successful_claims: "सफल क्लेम", recent_codes: "हाल के कोड (7 दिन)", no_recent: "पिछले 7 दिनों में कोई कोड नहीं।", drop_note: "यह कोड <b>केवल आपकी अपनी Stake API कुंजियों</b> (आपके स्लॉट) पर भेजा जाता है — किसी और के साथ साझा नहीं।", drop_code: "कोड", drop_code_ph: "उदा. abc123", drop_hint1: "<b>drop</b> एक सामान्य Stake बोनस-ड्रॉप कोड है। हम इसे आपके हर स्लॉट पर तुरंत क्लेम करते हैं।", drop_bonus_label: "यह बोनस कोड है", drop_hint2: "केवल साप्ताहिक/मासिक स्ट्रीम और बोनस-रिवॉर्ड कोड के लिए <b>ही</b> इसे चालू करें — ये drop के बजाय Stake के बोनस फ्लो से क्लेम होते हैं।", drop_btn: "मेरे स्लॉट पर ड्रॉप करें", enter_code: "कोड दर्ज करें", dropping: "ड्रॉप हो रहा है…", dropped_to: "भेजा गया", no_active_slots: "ड्रॉप करने के लिए कोई सक्रिय स्लॉट नहीं", invalid_code: "अमान्य कोड", drop_failed: "ड्रॉप विफल", lang_set: "भाषा सेट" },
    pl: { nav_accounts: "Konta", nav_buy: "Kup", nav_manage: "Zarządzaj", nav_stats: "Statystyki", nav_drop: "Drop", language: "Język", acc_subs: "Moje subskrypcje", buy_title: "Kup slot", manage_title: "Zarządzaj slotami", stats_title: "Panel odbioru", drop_title: "Wrzuć kod", dur_1day: "1 dzień (24h)", dur_days: "dni", dur_day: "dzień", plan_soon: "Wkrótce", plan_pricing_soon: "Cennik wkrótce", buy_choose_plan: "Wybierz plan", buy_plan_note: "Subskrypcja aktywuje się natychmiast po potwierdzeniu płatności i trwa przez okres planu.", acc_pool: "DOSTĘPNOŚĆ SLOTÓW", slots_available: "dostępnych slotów", acc_none_t: "Brak subskrypcji", acc_none_s: "Zautomatyzuj odbiór Stake z jednym z planów.", getstarted: "Zacznij", st_expired: "Wygasł", recent_trunc: "Pokazuję ostatnie 50", warn_slow: "⚠️ Za szybko — poczekaj kilka sekund, inaczej zostaniesz na chwilę wstrzymany.", reload_unavail: "⚠ Przeładowanie obecnie niedostępne", reload_next: "Następne przeładowanie:", reload_now: "dostępne teraz", pay_open_btn: "Otwórz stronę płatności", pay_copy: "Kopiuj link", pay_copied: "Link skopiowany", pay_link_hint: "Jeśli strona płatności się nie otworzyła, dotknij przycisku lub skopiuj poniższy link.", pay_no_url: "Nie udało się uzyskać linku do płatności — spróbuj ponownie.", pay_back: "Wstecz", time_left: "pozostało", no_expiry: "Bez wygaśnięcia", expires_prefix: "Wygasa", st_online: "Online", st_offline: "Offline", st_active: "Aktywny", err_dashboard: "Nie można załadować pulpitu.", err_plans: "Nie można załadować planów.", err_slots: "Nie można załadować slotów.", err_stats: "Nie można załadować statystyk.", err_generic: "Coś poszło nie tak", buy_step1: "Twój klucz API Stake", buy_key_ph: "Wklej klucz API Stake", buy_verify: "Zweryfikuj klucz", verify_enter_key: "Najpierw wpisz klucz API.", verifying: "Weryfikacja…", verified: "Zweryfikowano", scroll_cue: "Przewiń w dół, aby wybrać plan", verify_unavail: "Weryfikacja chwilowo niedostępna — spróbuj wkrótce.", verify_invalid: "Nieprawidłowy klucz API — sprawdź i spróbuj ponownie.", verify_failed: "Weryfikacja nie powiodła się — spróbuj ponownie.", buy_step3: "Skonfiguruj ten slot", buy_pay: "Przejdź do płatności", cfg_wc: "Waluta wypłaty", cfg_rc: "Waluta reload", cfg_min: "Min. wartość do odbioru (0 = wszystko)", cfg_av: "Auto Vault", cfg_ab: "Auto Bonus", cfg_ar: "Auto Reload", pay_need: "Najpierw zweryfikuj klucz i wybierz plan", creating_order: "Tworzenie zamówienia…", already_active: "Już aktywne", no_slots_now: "Brak wolnych slotów", pay_failed: "Nie można rozpocząć płatności", wait_pay_t: "Oczekiwanie na płatność…", wait_pay_s: "Dokończ płatność w otwartym oknie. Ten ekran odświeży się sam.", sub_active_t: "Subskrypcja aktywna!", sub_active_s: "teraz odbiera.", your_slot: "Twój slot", view_subs: "Zobacz subskrypcje", pay_not_done: "Płatność niedokończona", mng_none_t: "Brak slotów do zarządzania", mng_none_s: "Najpierw kup slot, potem skonfiguruj tutaj.", slot_expired_toast: "Ten slot wygasł", slot_word: "Slot", mng_replace_key: "Zmień klucz API (opcjonalnie — ponowna weryfikacja)", mng_replace_ph: "Zostaw puste, aby zachować", save_changes: "Zapisz zmiany", saving: "Zapisywanie…", saved: "Zapisano", mng_verify_fail: "Nie zweryfikowano nowego klucza", save_failed: "Zapis nieudany", no_earnings: "Brak zarobków", win_24h: "24h / Dziś", win_7d: "7 dni", win_30d: "30 dni", type_all: "Wszystkie", type_drop: "Dropy", type_reload: "Reload", claimed: "Odebrano", total_earned: "ŁĄCZNIE", successful_claims: "udanych odbiorów", recent_codes: "Ostatnie kody (7 dni)", no_recent: "Brak kodów z 7 dni.", drop_note: "Ten kod trafia <b>tylko do Twoich kluczy API Stake</b> (Twoich slotów) — nigdy do nikogo innego.", drop_code: "Kod", drop_code_ph: "np. abc123", drop_hint1: "<b>Drop</b> to zwykły kod bonus-drop Stake. Odbieramy go natychmiast na każdym slocie.", drop_bonus_label: "To kod bonusowy", drop_hint2: "Włącz to <b>tylko</b> dla tygodniowych/miesięcznych kodów stream i bonus — odbierane są przez bonusowy tryb Stake, nie drop.", drop_btn: "Wrzuć na moje sloty", enter_code: "Wpisz kod", dropping: "Wrzucanie…", dropped_to: "Wysłano do", no_active_slots: "Brak aktywnych slotów", invalid_code: "Nieprawidłowy kod", drop_failed: "Wrzucenie nieudane", lang_set: "Ustawiono język" },
    vi: { nav_accounts: "Tài khoản", nav_buy: "Mua", nav_manage: "Quản lý", nav_stats: "Thống kê", nav_drop: "Thả mã", language: "Ngôn ngữ", acc_subs: "Gói của tôi", buy_title: "Mua slot", manage_title: "Quản lý slot", stats_title: "Bảng nhận thưởng", drop_title: "Thả mã", dur_1day: "1 Ngày (24h)", dur_days: "Ngày", dur_day: "ngày", plan_soon: "Sắp có", plan_pricing_soon: "Giá sắp có", buy_choose_plan: "Chọn gói", buy_plan_note: "Gói kích hoạt ngay khi thanh toán được xác nhận và chạy trong thời hạn gói.", acc_pool: "SỐ SLOT CÒN TRỐNG", slots_available: "slot còn trống", acc_none_t: "Chưa có gói nào", acc_none_s: "Tự động nhận thưởng Stake với một trong các gói.", getstarted: "Bắt đầu", st_expired: "Hết hạn", recent_trunc: "Hiển thị 50 mới nhất", warn_slow: "⚠️ Bạn thao tác quá nhanh — vui lòng chờ vài giây, nếu không sẽ bị tạm dừng.", reload_unavail: "⚠ Hiện không thể nạp lại", reload_next: "Nạp lại tiếp theo:", reload_now: "có sẵn ngay", pay_open_btn: "Mở trang thanh toán", pay_copy: "Sao chép liên kết", pay_copied: "Đã sao chép liên kết", pay_link_hint: "Nếu trang thanh toán không mở, hãy nhấn nút hoặc sao chép liên kết bên dưới.", pay_no_url: "Không lấy được liên kết thanh toán — vui lòng thử lại.", pay_back: "Quay lại", time_left: "còn lại", no_expiry: "Không hết hạn", expires_prefix: "Hết hạn", st_online: "Trực tuyến", st_offline: "Ngoại tuyến", st_active: "Đang hoạt động", err_dashboard: "Không tải được bảng điều khiển.", err_plans: "Không tải được gói.", err_slots: "Không tải được slot.", err_stats: "Không tải được thống kê.", err_generic: "Đã xảy ra lỗi", buy_step1: "Khóa API Stake của bạn", buy_key_ph: "Dán khóa API Stake", buy_verify: "Xác minh khóa", verify_enter_key: "Nhập khóa API trước.", verifying: "Đang xác minh…", verified: "Đã xác minh", scroll_cue: "Cuộn xuống để chọn gói", verify_unavail: "Xác minh tạm thời không khả dụng — vui lòng thử lại sau.", verify_invalid: "Khóa API không hợp lệ — kiểm tra và thử lại.", verify_failed: "Xác minh thất bại — thử lại.", buy_step3: "Cấu hình slot này", buy_pay: "Tiếp tục thanh toán", cfg_wc: "Tiền tệ rút", cfg_rc: "Tiền tệ reload", cfg_min: "Giá trị tối thiểu để nhận (0 = tất cả)", cfg_av: "Tự động Vault", cfg_ab: "Tự động Bonus", cfg_ar: "Tự động Reload", pay_need: "Xác minh khóa và chọn gói trước", creating_order: "Đang tạo đơn…", already_active: "Đã hoạt động", no_slots_now: "Hiện không còn slot", pay_failed: "Không thể bắt đầu thanh toán", wait_pay_t: "Đang chờ thanh toán…", wait_pay_s: "Hoàn tất thanh toán trong cửa sổ đã mở. Màn hình tự cập nhật.", sub_active_t: "Gói đã kích hoạt!", sub_active_s: "đang nhận thưởng.", your_slot: "Slot của bạn", view_subs: "Xem gói của tôi", pay_not_done: "Thanh toán chưa hoàn tất", mng_none_t: "Không có slot để quản lý", mng_none_s: "Mua slot trước, rồi cấu hình ở đây.", slot_expired_toast: "Slot này đã hết hạn", slot_word: "Slot", mng_replace_key: "Thay khóa API (tùy chọn — xác minh lại)", mng_replace_ph: "Để trống để giữ nguyên", save_changes: "Lưu thay đổi", saving: "Đang lưu…", saved: "Đã lưu", mng_verify_fail: "Không xác minh được khóa mới", save_failed: "Lưu thất bại", no_earnings: "Chưa có thu nhập", win_24h: "24h / Hôm nay", win_7d: "7 Ngày", win_30d: "30 Ngày", type_all: "Tất cả", type_drop: "Thả mã", type_reload: "Reload", claimed: "Đã nhận", total_earned: "TỔNG KIẾM", successful_claims: "lần nhận thành công", recent_codes: "Mã gần đây (7 ngày)", no_recent: "Không có mã trong 7 ngày.", drop_note: "Mã này chỉ gửi <b>đến khóa API Stake của bạn</b> (slot của bạn) — không chia sẻ với ai khác.", drop_code: "Mã", drop_code_ph: "ví dụ abc123", drop_hint1: "<b>Drop</b> là mã bonus-drop thường của Stake. Chúng tôi nhận ngay trên mọi slot của bạn.", drop_bonus_label: "Đây là mã bonus", drop_hint2: "Chỉ bật cho mã stream/bonus hàng tuần/tháng — chúng được nhận qua luồng bonus của Stake thay vì drop.", drop_btn: "Thả vào slot của tôi", enter_code: "Nhập mã", dropping: "Đang thả…", dropped_to: "Đã gửi tới", no_active_slots: "Không có slot hoạt động để thả", invalid_code: "Mã không hợp lệ", drop_failed: "Thả thất bại", lang_set: "Đã đặt ngôn ngữ" },
    es: { nav_accounts: "Cuentas", nav_buy: "Comprar", nav_manage: "Gestionar", nav_stats: "Estadísticas", nav_drop: "Soltar", language: "Idioma", acc_subs: "Mis suscripciones", buy_title: "Comprar slot", manage_title: "Gestionar slots", stats_title: "Panel de reclamos", drop_title: "Soltar un código", dur_1day: "1 Día (24h)", dur_days: "Días", dur_day: "día", plan_soon: "Pronto", plan_pricing_soon: "Precio pronto", buy_choose_plan: "Elige un plan", buy_plan_note: "Tu suscripción se activa en cuanto se confirma el pago y dura lo que el plan.", acc_pool: "DISPONIBILIDAD DE SLOTS", slots_available: "slots disponibles", acc_none_t: "Sin suscripciones", acc_none_s: "Automatiza tus reclamos de Stake con un plan.", getstarted: "Empezar", st_expired: "Expirado", recent_trunc: "Mostrando los últimos 50", warn_slow: "⚠️ Vas muy rápido — espera unos segundos o serás pausado un momento.", reload_unavail: "⚠ Recarga no disponible ahora", reload_next: "Próxima recarga:", reload_now: "disponible ahora", pay_open_btn: "Abrir página de pago", pay_copy: "Copiar enlace", pay_copied: "Enlace copiado", pay_link_hint: "Si la página de pago no se abrió, toca el botón o copia el enlace de abajo.", pay_no_url: "No se pudo obtener el enlace de pago — inténtalo de nuevo.", pay_back: "Atrás", time_left: "restante", no_expiry: "Sin caducidad", expires_prefix: "Caduca", st_online: "En línea", st_offline: "Sin conexión", st_active: "Activo", err_dashboard: "No se pudo cargar el panel.", err_plans: "No se pudieron cargar los planes.", err_slots: "No se pudieron cargar los slots.", err_stats: "No se pudieron cargar las estadísticas.", err_generic: "Algo salió mal", buy_step1: "Tu clave API de Stake", buy_key_ph: "Pega tu clave API de Stake", buy_verify: "Verificar clave", verify_enter_key: "Ingresa tu clave API primero.", verifying: "Verificando…", verified: "Verificado", scroll_cue: "Desplázate para elegir tu plan", verify_unavail: "Verificación no disponible temporalmente — inténtalo pronto.", verify_invalid: "Clave API inválida — revisa e inténtalo de nuevo.", verify_failed: "Falló la verificación — inténtalo de nuevo.", buy_step3: "Configura este slot", buy_pay: "Continuar al pago", cfg_wc: "Moneda de retiro", cfg_rc: "Moneda de reload", cfg_min: "Valor mínimo a reclamar (0 = todo)", cfg_av: "Auto Vault", cfg_ab: "Auto Bono", cfg_ar: "Auto Reload", pay_need: "Verifica una clave y elige un plan primero", creating_order: "Creando orden…", already_active: "Ya activo", no_slots_now: "No hay slots disponibles ahora", pay_failed: "No se pudo iniciar el pago", wait_pay_t: "Esperando el pago…", wait_pay_s: "Completa el pago en la ventana abierta. Esta pantalla se actualiza sola.", sub_active_t: "¡Suscripción activa!", sub_active_s: "ya está reclamando.", your_slot: "Tu slot", view_subs: "Ver mis suscripciones", pay_not_done: "Pago no completado", mng_none_t: "No hay slots para gestionar", mng_none_s: "Compra un slot primero y configúralo aquí.", slot_expired_toast: "Este slot ha expirado", slot_word: "Slot", mng_replace_key: "Reemplazar clave API (opcional — se reverifica)", mng_replace_ph: "Déjalo vacío para mantener", save_changes: "Guardar cambios", saving: "Guardando…", saved: "Guardado", mng_verify_fail: "No se pudo verificar la nueva clave", save_failed: "Error al guardar", no_earnings: "Sin ganancias aún", win_24h: "24h / Hoy", win_7d: "7 Días", win_30d: "30 Días", type_all: "Todos", type_drop: "Drops", type_reload: "Reload", claimed: "Reclamado", total_earned: "TOTAL GANADO", successful_claims: "reclamos exitosos", recent_codes: "Códigos recientes (7 días)", no_recent: "Sin códigos en 7 días.", drop_note: "Este código se envía <b>SOLO a tus claves API de Stake</b> (tus slots) — nunca a nadie más.", drop_code: "Código", drop_code_ph: "ej. abc123", drop_hint1: "Un <b>drop</b> es un código normal de bonus de Stake. Lo reclamamos al instante en todos tus slots.", drop_bonus_label: "Es un código de bono", drop_hint2: "Actívalo <b>solo</b> para códigos de stream y bonos semanales/mensuales — se reclaman por el flujo de bonos de Stake.", drop_btn: "Soltar a mis slots", enter_code: "Ingresa un código", dropping: "Soltando…", dropped_to: "Enviado a", no_active_slots: "No hay slots activos", invalid_code: "Código inválido", drop_failed: "Error al soltar", lang_set: "Idioma establecido" },
    it: { nav_accounts: "Account", nav_buy: "Acquista", nav_manage: "Gestisci", nav_stats: "Statistiche", nav_drop: "Drop", language: "Lingua", acc_subs: "I miei abbonamenti", buy_title: "Acquista slot", manage_title: "Gestisci slot", stats_title: "Pannello richieste", drop_title: "Rilascia un codice", dur_1day: "1 Giorno (24h)", dur_days: "Giorni", dur_day: "giorno", plan_soon: "Presto", plan_pricing_soon: "Prezzo presto", buy_choose_plan: "Scegli un piano", buy_plan_note: "L'abbonamento si attiva appena il pagamento è confermato e dura per la durata del piano.", acc_pool: "DISPONIBILITÀ SLOT", slots_available: "slot disponibili", acc_none_t: "Nessun abbonamento", acc_none_s: "Automatizza i tuoi claim Stake con un piano.", getstarted: "Inizia", st_expired: "Scaduto", recent_trunc: "Mostrando gli ultimi 50", warn_slow: "⚠️ Stai andando molto veloce — attendi qualche secondo, altrimenti verrai messo in pausa.", reload_unavail: "⚠ Ricarica non disponibile ora", reload_next: "Prossima ricarica:", reload_now: "disponibile ora", pay_open_btn: "Apri pagina di pagamento", pay_copy: "Copia link", pay_copied: "Link copiato", pay_link_hint: "Se la pagina di pagamento non si è aperta, tocca il pulsante o copia il link qui sotto.", pay_no_url: "Impossibile ottenere il link di pagamento — riprova.", pay_back: "Indietro", time_left: "rimanenti", no_expiry: "Nessuna scadenza", expires_prefix: "Scade", st_online: "Online", st_offline: "Offline", st_active: "Attivo", err_dashboard: "Impossibile caricare la dashboard.", err_plans: "Impossibile caricare i piani.", err_slots: "Impossibile caricare gli slot.", err_stats: "Impossibile caricare le statistiche.", err_generic: "Qualcosa è andato storto", buy_step1: "La tua chiave API Stake", buy_key_ph: "Incolla la chiave API Stake", buy_verify: "Verifica chiave", verify_enter_key: "Inserisci prima la chiave API.", verifying: "Verifica…", verified: "Verificato", scroll_cue: "Scorri per scegliere il piano", verify_unavail: "Verifica temporaneamente non disponibile — riprova a breve.", verify_invalid: "Chiave API non valida — controlla e riprova.", verify_failed: "Verifica fallita — riprova.", buy_step3: "Configura questo slot", buy_pay: "Vai al pagamento", cfg_wc: "Valuta di prelievo", cfg_rc: "Valuta reload", cfg_min: "Valore minimo da reclamare (0 = tutto)", cfg_av: "Auto Vault", cfg_ab: "Auto Bonus", cfg_ar: "Auto Reload", pay_need: "Verifica una chiave e scegli un piano", creating_order: "Creazione ordine…", already_active: "Già attivo", no_slots_now: "Nessuno slot disponibile ora", pay_failed: "Impossibile avviare il pagamento", wait_pay_t: "In attesa del pagamento…", wait_pay_s: "Completa il pagamento nella finestra aperta. Questa schermata si aggiorna da sola.", sub_active_t: "Abbonamento attivo!", sub_active_s: "ora sta reclamando.", your_slot: "Il tuo slot", view_subs: "Vedi i miei abbonamenti", pay_not_done: "Pagamento non completato", mng_none_t: "Nessuno slot da gestire", mng_none_s: "Acquista prima uno slot, poi configuralo qui.", slot_expired_toast: "Questo slot è scaduto", slot_word: "Slot", mng_replace_key: "Sostituisci chiave API (facoltativo — riverificata)", mng_replace_ph: "Lascia vuoto per mantenere", save_changes: "Salva modifiche", saving: "Salvataggio…", saved: "Salvato", mng_verify_fail: "Impossibile verificare la nuova chiave", save_failed: "Salvataggio non riuscito", no_earnings: "Nessun guadagno", win_24h: "24h / Oggi", win_7d: "7 Giorni", win_30d: "30 Giorni", type_all: "Tutti", type_drop: "Drop", type_reload: "Reload", claimed: "Reclamato", total_earned: "TOTALE GUADAGNATO", successful_claims: "claim riusciti", recent_codes: "Codici recenti (7 giorni)", no_recent: "Nessun codice negli ultimi 7 giorni.", drop_note: "Questo codice va <b>SOLO alle tue chiavi API Stake</b> (i tuoi slot) — mai a nessun altro.", drop_code: "Codice", drop_code_ph: "es. abc123", drop_hint1: "Un <b>drop</b> è un normale codice bonus Stake. Lo reclamiamo subito su ogni tuo slot.", drop_bonus_label: "È un codice bonus", drop_hint2: "Attivalo <b>solo</b> per codici stream e bonus settimanali/mensili — reclamati tramite il flusso bonus di Stake.", drop_btn: "Rilascia ai miei slot", enter_code: "Inserisci un codice", dropping: "Rilascio…", dropped_to: "Inviato a", no_active_slots: "Nessuno slot attivo", invalid_code: "Codice non valido", drop_failed: "Rilascio non riuscito", lang_set: "Lingua impostata" },
    pt: { nav_accounts: "Contas", nav_buy: "Comprar", nav_manage: "Gerir", nav_stats: "Estatísticas", nav_drop: "Soltar", language: "Idioma", acc_subs: "Minhas assinaturas", buy_title: "Comprar slot", manage_title: "Gerir slots", stats_title: "Painel de resgates", drop_title: "Soltar um código", dur_1day: "1 Dia (24h)", dur_days: "Dias", dur_day: "dia", plan_soon: "Em breve", plan_pricing_soon: "Preço em breve", buy_choose_plan: "Escolha um plano", buy_plan_note: "Sua assinatura ativa assim que o pagamento é confirmado e dura o período do plano.", acc_pool: "DISPONIBILIDADE DE SLOTS", slots_available: "slots disponíveis", acc_none_t: "Sem assinaturas", acc_none_s: "Automatize seus resgates da Stake com um plano.", getstarted: "Começar", st_expired: "Expirado", recent_trunc: "Mostrando os últimos 50", warn_slow: "⚠️ Você está muito rápido — aguarde alguns segundos, ou será pausado por um momento.", reload_unavail: "⚠ Recarga indisponível no momento", reload_next: "Próxima recarga:", reload_now: "disponível agora", pay_open_btn: "Abrir página de pagamento", pay_copy: "Copiar link", pay_copied: "Link copiado", pay_link_hint: "Se a página de pagamento não abriu, toque no botão ou copie o link abaixo.", pay_no_url: "Não foi possível obter o link de pagamento — tente novamente.", pay_back: "Voltar", time_left: "restante", no_expiry: "Sem expiração", expires_prefix: "Expira", st_online: "Online", st_offline: "Offline", st_active: "Ativo", err_dashboard: "Não foi possível carregar o painel.", err_plans: "Não foi possível carregar os planos.", err_slots: "Não foi possível carregar os slots.", err_stats: "Não foi possível carregar as estatísticas.", err_generic: "Algo deu errado", buy_step1: "Sua chave API da Stake", buy_key_ph: "Cole sua chave API da Stake", buy_verify: "Verificar chave", verify_enter_key: "Digite sua chave API primeiro.", verifying: "Verificando…", verified: "Verificado", scroll_cue: "Role para escolher seu plano", verify_unavail: "Verificação temporariamente indisponível — tente em breve.", verify_invalid: "Chave API inválida — verifique e tente novamente.", verify_failed: "Falha na verificação — tente novamente.", buy_step3: "Configure este slot", buy_pay: "Ir para o pagamento", cfg_wc: "Moeda de saque", cfg_rc: "Moeda de reload", cfg_min: "Valor mínimo para resgatar (0 = tudo)", cfg_av: "Auto Vault", cfg_ab: "Auto Bônus", cfg_ar: "Auto Reload", pay_need: "Verifique uma chave e escolha um plano", creating_order: "Criando pedido…", already_active: "Já ativo", no_slots_now: "Sem slots disponíveis agora", pay_failed: "Não foi possível iniciar o pagamento", wait_pay_t: "Aguardando pagamento…", wait_pay_s: "Conclua o pagamento na janela aberta. Esta tela atualiza sozinha.", sub_active_t: "Assinatura ativa!", sub_active_s: "agora está resgatando.", your_slot: "Seu slot", view_subs: "Ver minhas assinaturas", pay_not_done: "Pagamento não concluído", mng_none_t: "Sem slots para gerir", mng_none_s: "Compre um slot primeiro e configure aqui.", slot_expired_toast: "Este slot expirou", slot_word: "Slot", mng_replace_key: "Substituir chave API (opcional — reverificada)", mng_replace_ph: "Deixe em branco para manter", save_changes: "Salvar alterações", saving: "Salvando…", saved: "Salvo", mng_verify_fail: "Não foi possível verificar a nova chave", save_failed: "Falha ao salvar", no_earnings: "Sem ganhos ainda", win_24h: "24h / Hoje", win_7d: "7 Dias", win_30d: "30 Dias", type_all: "Todos", type_drop: "Drops", type_reload: "Reload", claimed: "Resgatado", total_earned: "TOTAL GANHO", successful_claims: "resgates bem-sucedidos", recent_codes: "Códigos recentes (7 dias)", no_recent: "Sem códigos nos últimos 7 dias.", drop_note: "Este código vai <b>APENAS para suas chaves API da Stake</b> (seus slots) — nunca para outros.", drop_code: "Código", drop_code_ph: "ex. abc123", drop_hint1: "Um <b>drop</b> é um código de bônus normal da Stake. Resgatamos na hora em todos os seus slots.", drop_bonus_label: "É um código de bônus", drop_hint2: "Ative <b>apenas</b> para códigos de stream e bônus semanais/mensais — resgatados pelo fluxo de bônus da Stake.", drop_btn: "Soltar nos meus slots", enter_code: "Digite um código", dropping: "Soltando…", dropped_to: "Enviado para", no_active_slots: "Sem slots ativos", invalid_code: "Código inválido", drop_failed: "Falha ao soltar", lang_set: "Idioma definido" },
    fr: { nav_accounts: "Comptes", nav_buy: "Acheter", nav_manage: "Gérer", nav_stats: "Stats", nav_drop: "Drop", language: "Langue", acc_subs: "Mes abonnements", buy_title: "Acheter un slot", manage_title: "Gérer les slots", stats_title: "Tableau de réclamations", drop_title: "Larguer un code", dur_1day: "1 Jour (24h)", dur_days: "Jours", dur_day: "jour", plan_soon: "Bientôt", plan_pricing_soon: "Prix bientôt", buy_choose_plan: "Choisir un plan", buy_plan_note: "Votre abonnement s'active dès la confirmation du paiement et dure la durée du plan.", acc_pool: "DISPONIBILITÉ DES SLOTS", slots_available: "slots disponibles", acc_none_t: "Aucun abonnement", acc_none_s: "Automatisez vos réclamations Stake avec un plan.", getstarted: "Commencer", st_expired: "Expiré", recent_trunc: "Affichage des 50 derniers", warn_slow: "⚠️ Vous allez très vite — patientez quelques secondes, sinon vous serez suspendu un instant.", reload_unavail: "⚠ Rechargement indisponible actuellement", reload_next: "Prochain rechargement:", reload_now: "disponible maintenant", pay_open_btn: "Ouvrir la page de paiement", pay_copy: "Copier le lien", pay_copied: "Lien copié", pay_link_hint: "Si la page de paiement ne s'est pas ouverte, appuyez sur le bouton ou copiez le lien ci-dessous.", pay_no_url: "Impossible d'obtenir le lien de paiement — veuillez réessayer.", pay_back: "Retour", time_left: "restant", no_expiry: "Pas d'expiration", expires_prefix: "Expire", st_online: "En ligne", st_offline: "Hors ligne", st_active: "Actif", err_dashboard: "Impossible de charger le tableau de bord.", err_plans: "Impossible de charger les plans.", err_slots: "Impossible de charger les slots.", err_stats: "Impossible de charger les stats.", err_generic: "Une erreur s'est produite", buy_step1: "Votre clé API Stake", buy_key_ph: "Collez votre clé API Stake", buy_verify: "Vérifier la clé", verify_enter_key: "Entrez d'abord votre clé API.", verifying: "Vérification…", verified: "Vérifié", scroll_cue: "Faites défiler pour choisir", verify_unavail: "Vérification indisponible — réessayez bientôt.", verify_invalid: "Clé API invalide — vérifiez et réessayez.", verify_failed: "Échec de la vérification — réessayez.", buy_step3: "Configurer ce slot", buy_pay: "Continuer vers le paiement", cfg_wc: "Devise de retrait", cfg_rc: "Devise de reload", cfg_min: "Valeur min. à réclamer (0 = tout)", cfg_av: "Auto Vault", cfg_ab: "Auto Bonus", cfg_ar: "Auto Reload", pay_need: "Vérifiez une clé et choisissez un plan", creating_order: "Création de la commande…", already_active: "Déjà actif", no_slots_now: "Aucun slot disponible", pay_failed: "Impossible de démarrer le paiement", wait_pay_t: "En attente du paiement…", wait_pay_s: "Terminez le paiement dans la fenêtre ouverte. Cet écran se met à jour seul.", sub_active_t: "Abonnement actif !", sub_active_s: "réclame maintenant.", your_slot: "Votre slot", view_subs: "Voir mes abonnements", pay_not_done: "Paiement non effectué", mng_none_t: "Aucun slot à gérer", mng_none_s: "Achetez d'abord un slot, puis configurez-le ici.", slot_expired_toast: "Ce slot a expiré", slot_word: "Slot", mng_replace_key: "Remplacer la clé API (facultatif — revérifiée)", mng_replace_ph: "Laissez vide pour conserver", save_changes: "Enregistrer", saving: "Enregistrement…", saved: "Enregistré", mng_verify_fail: "Impossible de vérifier la nouvelle clé", save_failed: "Échec de l'enregistrement", no_earnings: "Aucun gain", win_24h: "24h / Aujourd'hui", win_7d: "7 Jours", win_30d: "30 Jours", type_all: "Tous", type_drop: "Drops", type_reload: "Reload", claimed: "Réclamé", total_earned: "TOTAL GAGNÉ", successful_claims: "réclamations réussies", recent_codes: "Codes récents (7 jours)", no_recent: "Aucun code ces 7 derniers jours.", drop_note: "Ce code est envoyé <b>UNIQUEMENT à vos clés API Stake</b> (vos slots) — jamais à d'autres.", drop_code: "Code", drop_code_ph: "ex. abc123", drop_hint1: "Un <b>drop</b> est un code bonus Stake normal. Nous le réclamons instantanément sur tous vos slots.", drop_bonus_label: "C'est un code bonus", drop_hint2: "Activez-le <b>uniquement</b> pour les codes stream et bonus hebdo/mensuels — réclamés via le flux bonus de Stake.", drop_btn: "Larguer sur mes slots", enter_code: "Entrez un code", dropping: "Largage…", dropped_to: "Envoyé à", no_active_slots: "Aucun slot actif", invalid_code: "Code invalide", drop_failed: "Échec du largage", lang_set: "Langue définie" },
    tr: { nav_accounts: "Hesaplar", nav_buy: "Satın Al", nav_manage: "Yönet", nav_stats: "İstatistik", nav_drop: "Drop", language: "Dil", acc_subs: "Aboneliklerim", buy_title: "Slot Satın Al", manage_title: "Slotları Yönet", stats_title: "Talep Paneli", drop_title: "Kod Bırak", dur_1day: "1 Gün (24s)", dur_days: "Gün", dur_day: "gün", plan_soon: "Yakında", plan_pricing_soon: "Fiyat yakında", buy_choose_plan: "Plan seç", buy_plan_note: "Aboneliğiniz ödeme onaylandığı anda etkinleşir ve plan süresi boyunca çalışır.", acc_pool: "SLOT DURUMU", slots_available: "slot mevcut", acc_none_t: "Henüz abonelik yok", acc_none_s: "Planlarımızla Stake taleplerinizi otomatikleştirin.", getstarted: "Başla", st_expired: "Süresi doldu", recent_trunc: "Son 50 gösteriliyor", warn_slow: "⚠️ Çok hızlısınız — birkaç saniye bekleyin, yoksa kısa süre duraklatılırsınız.", reload_unavail: "⚠ Yeniden yükleme şu anda kullanılamıyor", reload_next: "Sonraki yükleme:", reload_now: "şimdi kullanılabilir", pay_open_btn: "Ödeme sayfasını aç", pay_copy: "Bağlantıyı kopyala", pay_copied: "Bağlantı kopyalandı", pay_link_hint: "Ödeme sayfası açılmadıysa düğmeye dokunun veya aşağıdaki bağlantıyı kopyalayın.", pay_no_url: "Ödeme bağlantısı alınamadı — lütfen tekrar deneyin.", pay_back: "Geri", time_left: "kaldı", no_expiry: "Süresiz", expires_prefix: "Bitiş", st_online: "Çevrimiçi", st_offline: "Çevrimdışı", st_active: "Aktif", err_dashboard: "Panel yüklenemedi.", err_plans: "Planlar yüklenemedi.", err_slots: "Slotlar yüklenemedi.", err_stats: "İstatistikler yüklenemedi.", err_generic: "Bir şeyler ters gitti", buy_step1: "Stake API anahtarınız", buy_key_ph: "Stake API anahtarınızı yapıştırın", buy_verify: "Anahtarı doğrula", verify_enter_key: "Önce API anahtarınızı girin.", verifying: "Doğrulanıyor…", verified: "Doğrulandı", scroll_cue: "Plan seçmek için aşağı kaydırın", verify_unavail: "Doğrulama geçici olarak kullanılamıyor — birazdan deneyin.", verify_invalid: "Geçersiz API anahtarı — kontrol edip tekrar deneyin.", verify_failed: "Doğrulama başarısız — tekrar deneyin.", buy_step3: "Bu slotu yapılandır", buy_pay: "Ödemeye devam et", cfg_wc: "Çekim para birimi", cfg_rc: "Reload para birimi", cfg_min: "Talep için min. değer (0 = tümü)", cfg_av: "Otomatik Kasa", cfg_ab: "Otomatik Bonus", cfg_ar: "Otomatik Reload", pay_need: "Önce anahtar doğrulayın ve plan seçin", creating_order: "Sipariş oluşturuluyor…", already_active: "Zaten aktif", no_slots_now: "Şu anda uygun slot yok", pay_failed: "Ödeme başlatılamadı", wait_pay_t: "Ödeme bekleniyor…", wait_pay_s: "Açılan pencerede ödemeyi tamamlayın. Bu ekran otomatik güncellenir.", sub_active_t: "Abonelik etkin!", sub_active_s: "şimdi talep ediyor.", your_slot: "Slotunuz", view_subs: "Aboneliklerimi gör", pay_not_done: "Ödeme tamamlanmadı", mng_none_t: "Yönetilecek slot yok", mng_none_s: "Önce slot alın, sonra burada yapılandırın.", slot_expired_toast: "Bu slotun süresi doldu", slot_word: "Slot", mng_replace_key: "API anahtarını değiştir (isteğe bağlı — yeniden doğrulanır)", mng_replace_ph: "Mevcut için boş bırakın", save_changes: "Değişiklikleri kaydet", saving: "Kaydediliyor…", saved: "Kaydedildi", mng_verify_fail: "Yeni anahtar doğrulanamadı", save_failed: "Kaydetme başarısız", no_earnings: "Henüz kazanç yok", win_24h: "24s / Bugün", win_7d: "7 Gün", win_30d: "30 Gün", type_all: "Tümü", type_drop: "Droplar", type_reload: "Reload", claimed: "Alındı", total_earned: "TOPLAM KAZANÇ", successful_claims: "başarılı talep", recent_codes: "Son kodlar (7 gün)", no_recent: "Son 7 günde kod yok.", drop_note: "Bu kod <b>YALNIZCA kendi Stake API anahtarlarınıza</b> (slotlarınıza) gönderilir — asla başkasına.", drop_code: "Kod", drop_code_ph: "örn. abc123", drop_hint1: "<b>Drop</b>, normal bir Stake bonus-drop kodudur. Tüm slotlarınızda anında talep ederiz.", drop_bonus_label: "Bu bir bonus kodu", drop_hint2: "Bunu <b>yalnızca</b> haftalık/aylık yayın ve bonus kodları için açın — drop yerine Stake bonus akışıyla alınır.", drop_btn: "Slotlarıma bırak", enter_code: "Bir kod girin", dropping: "Bırakılıyor…", dropped_to: "Gönderildi", no_active_slots: "Aktif slot yok", invalid_code: "Geçersiz kod", drop_failed: "Bırakma başarısız", lang_set: "Dil ayarlandı" }
  };
  function t(key) {
    var l = lang();
    return (MSG[l] && MSG[l][key]) || MSG.en[key] || key;
  }

  // ── helpers ────────────────────────────────────────────────────────────
  function esc(s) {
    if (s === null || s === undefined) return "";
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function el(id) { return document.getElementById(id); }
  function stale(seq) { return seq !== renderSeq; }
  function view(html) {
    var v = el("view"); v.innerHTML = html; v.setAttribute("aria-busy", "false");
    // Content height just changed → re-evaluate the floating scroll-down cue.
    if (typeof requestAnimationFrame === "function") requestAnimationFrame(updateScrollCue);
    else updateScrollCue();
  }
  // ── floating scroll-down cue ─────────────────────────────────────────────
  // A small bouncing down-arrow fixed at the bottom-centre, shown on ANY view
  // that has more content below and hidden once the user reaches the bottom.
  // The page scrolls on the window (#view has no overflow; #app grows past 100vh).
  function updateScrollCue() {
    var cue = el("scrolldown"); if (!cue) return;
    var d = document.scrollingElement || document.documentElement || document.body;
    var vh = window.innerHeight || d.clientHeight || 0;
    var more = (d.scrollHeight - vh) > 24;
    var atBottom = (d.scrollTop + vh) >= (d.scrollHeight - 24);
    cue.classList.toggle("hidden", !(more && !atBottom));
  }
  function initScrollCue() {
    if (el("scrolldown")) return;   // create once
    var cue = document.createElement("div");
    cue.id = "scrolldown"; cue.className = "hidden";
    cue.setAttribute("aria-hidden", "true");
    cue.innerHTML = icon("chevron", "cue-arrow");
    cue.addEventListener("click", function () {
      try { window.scrollBy({ top: Math.round((window.innerHeight || 600) * 0.8), behavior: "smooth" }); }
      catch (e) { window.scrollBy(0, 400); }
    });
    document.body.appendChild(cue);
    window.addEventListener("scroll", updateScrollCue, { passive: true });
    window.addEventListener("resize", updateScrollCue);
    updateScrollCue();
  }
  function toast(msg, bad) {
    var t = el("toast");
    t.textContent = msg; t.className = "toast" + (bad ? " bad" : "");
    clearTimeout(t._t); t._t = setTimeout(function () { t.className = "toast hidden"; }, 3200);
  }
  function haptic(kind) { try { tg && tg.HapticFeedback && tg.HapticFeedback.notificationOccurred(kind); } catch (e) {} }
  function icon(name, cls) { return '<svg class="' + (cls ? "ic " + cls : "ic") + '" aria-hidden="true" focusable="false"><use href="#i-' + name + '"/></svg>'; }
  function lang() { try { return localStorage.getItem("scc_lang") || "en"; } catch (e) { return "en"; } }
  function langLabel(code) { for (var i = 0; i < LANGS.length; i++) if (LANGS[i][0] === code) return LANGS[i][1]; return "🇬🇧 English"; }
  // Relative countdown to the minute: "3d 5h 33m left" / "5h 33m left" / "33m left" / "<1m left".
  // Malformed input can NEVER produce a countdown — null/"" → "No expiry", unparseable → "—".
  function fmtExpiry(iso) {
    if (!iso) return t("no_expiry");
    if (typeof iso !== "string") return "—";        // non-string can never be a countdown
    var tms = Date.parse(iso); if (isNaN(tms)) return "—";
    var ms = tms - Date.now();
    if (ms <= 0) return t("st_expired");
    var d = Math.floor(ms / 86400000),
        h = Math.floor((ms % 86400000) / 3600000),
        m = Math.floor((ms % 3600000) / 60000);
    if (d >= 1) return d + "d " + h + "h " + m + "m " + t("time_left");
    if (h >= 1) return h + "h " + m + "m " + t("time_left");
    if (m >= 1) return m + "m " + t("time_left");
    return "<1m " + t("time_left");            // 0 < ms < 60000 — still live, not "0m"
  }
  // Absolute expiry in the VIEWER'S local timezone (toLocaleString always uses the device tz).
  // Locale follows the selected UI language; guards against malformed input (no "Invalid Date").
  function fmtExpiryAbs(iso) {
    if (!iso || typeof iso !== "string") return "";
    var tms = Date.parse(iso); if (isNaN(tms)) return "";
    var opts = { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" };
    try { return new Date(tms).toLocaleString(lang(), opts); }
    catch (e) { try { return new Date(tms).toLocaleString(undefined, opts); } catch (e2) { return ""; } }
  }
  function isExpiredIso(iso) { var tms = Date.parse(iso); return !isNaN(tms) && tms <= Date.now(); }
  // Second line under a card: "Expires <local time>" (or "Expired <local time>"). Hidden if no/bad date.
  function secondLineIso(iso) {
    var abs = fmtExpiryAbs(iso); if (!abs) return "";
    return (isExpiredIso(iso) ? t("st_expired") : t("expires_prefix")) + " " + abs;
  }
  function secondLine(s) {
    var abs = fmtExpiryAbs(s.expires_at); if (!abs) return "";
    return ((s.expired || isExpiredIso(s.expires_at)) ? t("st_expired") : t("expires_prefix")) + " " + abs;
  }
  // Live minute-accurate refresh — STRICT singleton, self-stops when no expiry nodes are on screen.
  function refreshExpiry() {
    var cds = document.querySelectorAll("[data-exp]");
    if (!cds.length) { stopExpiryTick(); return; }
    Array.prototype.forEach.call(cds, function (n) { n.textContent = fmtExpiry(n.getAttribute("data-exp")); });
    Array.prototype.forEach.call(document.querySelectorAll("[data-exp-abs]"), function (n) {
      n.textContent = secondLineIso(n.getAttribute("data-exp-abs"));
    });
  }
  function startExpiryTick() { if (expiryTick) return; expiryTick = setInterval(refreshExpiry, 60000); }
  function stopExpiryTick() { if (expiryTick) { clearInterval(expiryTick); expiryTick = null; } }
  // ── proactive "slow down" warning panel (dismissible, debounced) ───────────
  var _warnLast = 0;
  function showSlowDown() {
    var now = Date.now();
    if (now - _warnLast < 20000) return;   // debounce ~20s (not spammy; reappears if they keep hammering)
    _warnLast = now;
    var bar = el("warnbar");
    if (!bar) {
      bar = document.createElement("div");
      bar.id = "warnbar"; bar.className = "hidden";
      bar.innerHTML = '<span class="warn-msg"></span>' +
        '<button class="warn-x" type="button" aria-label="Dismiss">✕</button>';
      document.body.appendChild(bar);
      bar.querySelector(".warn-x").addEventListener("click", function () { bar.classList.add("hidden"); });
    }
    bar.querySelector(".warn-msg").textContent = t("warn_slow");
    bar.classList.remove("hidden");
  }
  // Countdown to the next reload from an absolute ms timestamp.
  function fmtReloadNext(ms) {
    var d = Number(ms) - Date.now();
    if (!ms || isNaN(d)) return "";
    if (d <= 0) return t("reload_now");
    var h = Math.floor(d / 3600000), m = Math.floor((d % 3600000) / 60000);
    if (h >= 1) return h + "h " + m + "m";
    if (m >= 1) return m + "m";
    return "<1m";
  }
  // Reload status line for a slot card: unavailable warning OR next-reload time.
  function reloadNote(s) {
    if (s.reload_unavailable) return '<div class="reload-note bad">' + esc(t("reload_unavail")) + '</div>';
    if (s.reload_available && s.reload_next_ms) {
      return '<div class="reload-note">' + esc(t("reload_next") + " " + fmtReloadNext(s.reload_next_ms)) + '</div>';
    }
    return "";
  }
  function num(v) { var n = Number(v); return isNaN(n) ? 0 : n; }

  // ── theme ────────────────────────────────────────────────────────────────
  function applyTheme() {
    var scheme = (tg && tg.colorScheme === "light") ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", scheme);
    var m = document.querySelector('meta[name="theme-color"]');
    if (m) m.setAttribute("content", scheme === "light" ? "#f3f6fa" : "#0a0f17");
  }

  // ── API layer ──────────────────────────────────────────────────────────
  function api(path, opts) {
    opts = opts || {};
    var headers = {};
    if (token) headers["Authorization"] = "Bearer " + token;
    if (opts.body) headers["Content-Type"] = "application/json";
    return fetch(API + path, {
      method: opts.method || "GET", headers: headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false, code: "BACKEND_UNAVAILABLE" }; })
        .then(function (j) {
          if (j && j.ok) {
            if (j.warn === "slow_down") { try { showSlowDown(); } catch (e) {} }
            return j.data;
          }
          var e = new Error((j && j.error) || "error");
          e.code = (j && j.code) || "BACKEND_UNAVAILABLE"; e.status = r.status; throw e;
        });
    });
  }
  function readInitData() {
    if (tg && tg.initData) return tg.initData;
    try {
      var h = (window.location.hash || "").replace(/^#/, "");
      var m = /(?:^|&)tgWebAppData=([^&]*)/.exec(h);
      if (m && m[1]) return decodeURIComponent(m[1]);
    } catch (e) {}
    return "";
  }
  function authenticate() {
    var initData = readInitData();
    if (!initData) {
      var e = new Error("Please open this Mini App inside Telegram (menu button or 'Open App').");
      e.noInit = true; return Promise.reject(e);
    }
    return api("/auth", { method: "POST", body: { initData: initData } }).then(function (d) {
      token = d.token; isAdmin = !!d.is_admin; scheduleRefresh(); return d;
    });
  }
  function scheduleRefresh() {
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = setTimeout(function () {
      api("/refresh", { method: "POST" }).then(function (d) {
        token = d.token; isAdmin = !!d.is_admin; scheduleRefresh();
      }).catch(function () {
        authenticate().catch(function () { fatal("Session expired. Please reopen the app."); });
      });
    }, 25 * 60 * 1000);
  }
  function fatal(msg) { view('<div class="empty">' + icon("alert", "empty-ic") + '<div class="empty-t">'+esc(t("err_generic"))+'</div><div class="empty-s">' + esc(msg) + '</div></div>'); }

  // ── header (account chip + language pill) ────────────────────────────────
  function renderHeader() {
    var u = (tg && tg.initDataUnsafe && tg.initDataUnsafe.user) || {};
    var name = esc(u.first_name || "Account");
    var uname = u.username ? ("@" + esc(u.username)) : ("ID " + esc(u.id || "—"));
    var hb = el("headerbar");
    hb.className = "headerbar";
    hb.innerHTML =
      '<div class="acct"><div class="acct-av">' + esc((u.first_name || "A").charAt(0).toUpperCase()) + '</div>' +
      '<div class="acct-meta"><div class="acct-name">' + name + '</div><div class="acct-sub">' + uname + '</div></div></div>' +
      '<button class="lang-pill" id="langPill" type="button">' + esc(langLabel(lang())) + '</button>';
    el("langPill").addEventListener("click", openLangSheet);
  }
  function openLangSheet() {
    var rows = LANGS.map(function (l) {
      return '<button class="lang-row" data-code="' + l[0] + '" type="button">' + esc(l[1]) +
        (l[0] === lang() ? ' <span class="lang-cur">' + icon("check") + '</span>' : '') + '</button>';
    }).join("");
    openSheet(t("language"), '<div class="lang-list">' + rows + '</div>');
    Array.prototype.forEach.call(document.querySelectorAll(".lang-row"), function (b) {
      b.addEventListener("click", function () {
        try { localStorage.setItem("scc_lang", b.getAttribute("data-code")); } catch (e) {}
        closeSheet();
        // Re-render the WHOLE Mini App in the new language (nav + current view).
        renderHeader(); renderNav(); markNav(); go(current);
        toast(t("lang_set"));
      });
    });
  }

  // ── bottom sheet (modal) ─────────────────────────────────────────────────
  function openSheet(title, bodyHtml) {
    var s = el("sheet");
    s.innerHTML = '<div class="sheet-card"><div class="sheet-head"><div class="sheet-title">' + esc(title) +
      '</div><button class="sheet-x" id="sheetX" type="button">×</button></div><div class="sheet-body">' + bodyHtml + '</div></div>';
    s.className = "sheet";
    el("sheetX").addEventListener("click", closeSheet);
    s.addEventListener("click", function (e) { if (e.target === s) closeSheet(); });
  }
  function closeSheet() { var s = el("sheet"); s.className = "sheet hidden"; s.innerHTML = ""; }

  // ── bottom navigation ─────────────────────────────────────────────────────
  var TABS = [
    ["accounts", "home"], ["buy", "wallet"],
    ["manage", "key"], ["stats", "chart"], ["drop", "ticket"]
  ];
  function renderNav() {
    el("bottomnav").innerHTML = TABS.map(function (tb) {
      return '<button class="navbtn" data-tab="' + tb[0] + '" type="button">' + icon(tb[1]) +
        '<span>' + esc(t("nav_" + tb[0])) + '</span></button>';
    }).join("");
    Array.prototype.forEach.call(document.querySelectorAll(".navbtn"), function (b) {
      b.addEventListener("click", function () { go(b.getAttribute("data-tab")); });
    });
  }
  function markNav() {
    Array.prototype.forEach.call(document.querySelectorAll(".navbtn"), function (b) {
      b.classList.toggle("active", b.getAttribute("data-tab") === current);
    });
  }
  function go(tab) {
    current = tab; renderSeq++; markNav();
    stopExpiryTick();            // clear on EVERY view exit; the two expiry views re-arm after paint
    el("view").scrollTop = 0;
    try { window.scrollTo(0, 0); } catch (e) {}   // the window is the real scroller
    if (tab === "accounts") viewAccounts();
    else if (tab === "buy") viewBuy();
    else if (tab === "manage") viewManage();
    else if (tab === "stats") viewStats();
    else if (tab === "drop") viewDrop();
  }

  function loading() { view('<div class="loader"><div class="spinner"></div></div>'); }
  function planDurationText(p) {
    var d = Number(p.duration_days);
    if (!d) return "";
    return d === 1 ? t("dur_1day") : (d + " " + t("dur_days"));
  }
  function planTiles(plans, onPick) {
    return (plans || []).map(function (p, i) {
      var price = p.price_usd != null ? ("$" + p.price_usd) : t("plan_soon");
      // Show ≈/day only when the plan HAS a per-day rate. A priced plan without one
      // (e.g. Stream Special) shows nothing — not a misleading "$0.86/day" or "soon".
      var per = p.per_day_usd != null ? ("≈$" + p.per_day_usd + " / " + t("dur_day"))
        : (p.price_usd != null ? (p.features && p.features[0] ? p.features[0] : "") : t("plan_pricing_soon"));
      // Duration line — shown for Stream Special (and any 1-day plan) so buyers see
      // it's a 24h plan; other tiles already carry the duration in their label.
      var durLine = (p.code === "stream_special" || Number(p.duration_days) === 1)
        ? '<div class="plan-dur">⏱ ' + esc(planDurationText(p)) + '</div>' : '';
      return '<button class="plan" data-i="' + i + '" type="button"' + (p.price_usd == null ? ' disabled' : '') + '>' +
        '<div class="plan-badge">' + esc(p.badge || "") + '</div>' +
        '<div class="plan-name">' + esc(p.label) + '</div>' + durLine +
        '<div class="plan-price">' + esc(price) + '</div>' +
        '<div class="plan-per">' + esc(per) + '</div></button>';
    }).join("");
  }

  // ── Accounts (home) ───────────────────────────────────────────────────────
  function viewAccounts() {
    var seq = renderSeq; loading();
    Promise.all([api("/capacity").catch(function () { return null; }),
                 api("/slots").catch(function () { return { slots: [] }; })])
      .then(function (r) {
        if (stale(seq)) return;
        var cap = r[0] || {}, slots = (r[1] && r[1].slots) || [];
        var avail = cap.available != null ? cap.available : "—";
        var priceRow = (cap.plans || []).filter(function (p) { return p.price_usd != null; })
          .map(function (p) { return '<span class="pchip">' + esc(p.label) + ' <b>$' + esc(p.price_usd) + '</b></span>'; }).join("");
        var html = '<section class="cap"><div class="cap-dot"></div><div class="cap-main"><div class="cap-lbl">'+esc(t("acc_pool"))+'</div>' +
          '<div class="cap-val">' + esc(avail) + ' '+esc(t("slots_available"))+'</div></div>' + icon("shield", "cap-ic") + '</section>';
        if (priceRow) html += '<div class="prices">' + priceRow + '</div>';
        html += '<h2 class="sec">'+esc(t("acc_subs"))+'</h2>';
        if (!slots.length) {
          html += '<div class="empty"><div class="empty-t">'+esc(t("acc_none_t"))+'</div>' +
            '<div class="empty-s">'+esc(t("acc_none_s"))+'</div>' +
            '<button class="btn primary" id="getStarted" type="button">'+esc(t("getstarted"))+'</button></div>';
        } else {
          html += '<div class="subs">' + slots.map(subCard).join("") + '</div>';
        }
        view(html);
        startExpiryTick();
        var gs = el("getStarted"); if (gs) gs.addEventListener("click", function () { go("buy"); });
        Array.prototype.forEach.call(document.querySelectorAll(".sub-gear"), function (b) {
          b.addEventListener("click", function () { go("manage"); });
        });
      }).catch(function () { if (!stale(seq)) fatal(t("err_dashboard")); });
  }
  function subCard(s) {
    var online = s.online && !s.expired;
    var sl = secondLine(s);
    return '<div class="sub">' +
      '<div class="sub-top"><div class="sub-name">' + esc(s.stake_username || "—") + '</div>' +
      '<span class="dot ' + (online ? "on" : "off") + '"></span>' +
      '<span class="sub-status">' + (s.expired ? t("st_expired") : (online ? t("st_online") : t("st_offline"))) + '</span>' +
      '<button class="sub-gear" type="button" aria-label="Manage">' + icon("key") + '</button></div>' +
      '<div class="sub-meta"><span class="tag">' + esc(s.plan || "plan") + '</span>' +
      '<span class="tag alt" data-exp="' + esc(s.expires_at || "") + '">' + esc(fmtExpiry(s.expires_at)) + '</span>' +
      '<span class="tag ghost">' + esc(s.worker_label || "Worker") + '</span></div>' +
      reloadNote(s) +
      (sl ? '<div class="sub-exp" data-exp-abs="' + esc(s.expires_at || "") + '">' + esc(sl) + '</div>' : '') +
      '</div>';
  }

  // ── Buy ────────────────────────────────────────────────────────────────────
  var buyState = { token: null, username: null, plan: null, plans: [] };
  function viewBuy() {
    var seq = renderSeq; loading();
    api("/capacity").then(function (cap) {
      if (stale(seq)) return;
      buyState = { token: null, username: null, plan: null, plans: cap.plans || [] };
      renderBuyStep1(cap);
    }).catch(function () { if (!stale(seq)) fatal(t("err_plans")); });
  }
  function renderBuyStep1(cap) {
    var html = '<h2 class="sec">'+esc(t("buy_title"))+'</h2>' +
      '<div class="cap-mini">🟢 ' + esc(cap.available != null ? cap.available : "—") + ' slots available</div>' +
      '<div class="card"><label class="lab">1 · '+esc(t("buy_step1"))+'</label>' +
      '<input class="inp" id="buyKey" type="password" placeholder="'+esc(t("buy_key_ph"))+'" autocomplete="off">' +
      '<button class="btn" id="verifyBtn" type="button">'+esc(t("buy_verify"))+'</button>' +
      '<div class="verify-out" id="verifyOut"></div></div>' +
      '<div id="buyRest"></div>';
    view(html);
    el("verifyBtn").addEventListener("click", doVerify);
  }
  function doVerify() {
    var key = (el("buyKey").value || "").trim();
    var out = el("verifyOut");
    if (!key) { out.textContent = t("verify_enter_key"); out.className = "verify-out bad"; return; }
    out.textContent = t("verifying"); out.className = "verify-out";
    el("verifyBtn").disabled = true;
    api("/verify-token", { method: "POST", body: { token: key } }).then(function (d) {
      el("verifyBtn").disabled = false;
      if (d.valid) {
        buyState.token = key; buyState.username = d.username || null;
        out.innerHTML = icon("check") + " " + t("verified") + ": <b>" + esc(d.username || "account") + "</b>";
        out.className = "verify-out good"; haptic("success");
        renderBuyStep2();
        // Nudge the buyer to the plan section that just appeared below.
        setTimeout(function () {
          try { var pr = el("buyRest"); if (pr) pr.scrollIntoView({ behavior: "smooth", block: "start" }); } catch (e) {}
        }, 120);
      } else {
        buyState.token = null;
        out.textContent = d.reason === "unavailable"
          ? t("verify_unavail")
          : t("verify_invalid");
        out.className = "verify-out bad"; haptic("error");
      }
    }).catch(function () { el("verifyBtn").disabled = false; out.textContent = t("verify_failed"); out.className = "verify-out bad"; });
  }
  function renderBuyStep2() {
    var rest = el("buyRest");
    rest.innerHTML = '<label class="lab">2 · ' + esc(t("buy_choose_plan")) + '</label><div class="plans">' + planTiles(buyState.plans) + '</div>' +
      '<div class="hint">' + esc(t("buy_plan_note")) + '</div>' +
      '<div id="buyStep3"></div>';
    Array.prototype.forEach.call(rest.querySelectorAll(".plan"), function (b) {
      b.addEventListener("click", function () {
        Array.prototype.forEach.call(rest.querySelectorAll(".plan"), function (x) { x.classList.remove("sel"); });
        b.classList.add("sel");
        buyState.plan = buyState.plans[Number(b.getAttribute("data-i"))];
        renderBuyStep3();
      });
    });
  }
  // Full currency set — mirrors the userscript's AVAILABLE_CURRENCIES (17).
  var CURRENCIES = ["usdt", "btc", "eth", "ltc", "sol", "doge", "xrp", "trx", "eos",
                    "bnb", "usdc", "dai", "link", "shib", "uni", "pol", "trump"];
  function currencyOptions(sel) {
    return CURRENCIES.map(function (c) {
      return '<option value="' + c + '"' + (c === sel ? " selected" : "") + '>' + c.toUpperCase() + '</option>';
    }).join("");
  }
  function renderBuyStep3() {
    var s3 = el("buyStep3");
    s3.innerHTML = '<label class="lab">3 · '+esc(t("buy_step3"))+'</label><div class="card">' +
      cfgFields({}) +
      '<button class="btn primary" id="payBtn" type="button">'+esc(t("buy_pay"))+' · ' +
      (buyState.plan ? "$" + esc(buyState.plan.price_usd) : "") + '</button></div>';
    wireToggles(s3);   // FIX: Buy-flow toggles were never wired (Manage/Drop were)
    el("payBtn").addEventListener("click", doBuyPay);
  }
  function cfgFields(v) {
    v = v || {};
    return '<div class="row2"><div><label class="lab sm">'+esc(t("cfg_wc"))+'</label>' +
      '<select class="inp" id="cfgWc">' + currencyOptions(v.withdrawal_currency || "usdt") + '</select></div>' +
      '<div><label class="lab sm">'+esc(t("cfg_rc"))+'</label><select class="inp" id="cfgRc">' + currencyOptions(v.reload_currency || "usdt") + '</select></div></div>' +
      '<label class="lab sm">'+esc(t("cfg_min"))+'</label>' +
      '<input class="inp" id="cfgVf" type="number" min="0" step="0.01" value="' + esc(v.value_filter != null ? v.value_filter : "") + '" placeholder="0">' +
      toggleRow("cfgAv", t("cfg_av"), !!v.auto_vault) +
      toggleRow("cfgAb", t("cfg_ab"), !!v.auto_bonus) +
      toggleRow("cfgAr", t("cfg_ar"), !!v.auto_reload);
  }
  function toggleRow(id, label, on) {
    return '<div class="tog" data-id="' + id + '" data-on="' + (on ? 1 : 0) + '"><span>' + esc(label) +
      '</span><span class="sw ' + (on ? "on" : "") + '"><span class="knob"></span></span></div>';
  }
  function wireToggles(root) {
    Array.prototype.forEach.call((root || document).querySelectorAll(".tog"), function (t) {
      t.addEventListener("click", function () {
        var on = t.getAttribute("data-on") === "1" ? 0 : 1;
        t.setAttribute("data-on", String(on));
        t.querySelector(".sw").classList.toggle("on", !!on);
      });
    });
  }
  function readCfg() {
    function g(id) { var e = el(id); return e ? e.value : ""; }
    function tog(id) { var e = document.querySelector('.tog[data-id="' + id + '"]'); return !!(e && e.getAttribute("data-on") === "1"); }
    return {
      withdrawal_currency: g("cfgWc") || "usdt", reload_currency: g("cfgRc") || "usdt",
      value_filter: g("cfgVf") || null, auto_vault: tog("cfgAv"),
      auto_bonus: tog("cfgAb"), auto_reload: tog("cfgAr")
    };
  }
  function doBuyPay() {
    if (!buyState.token || !buyState.plan) { toast(t("pay_need"), true); return; }
    var cfg = readCfg();
    var btn = el("payBtn"); btn.disabled = true; btn.textContent = t("creating_order");
    api("/order/begin", { method: "POST", body: { plan_code: buyState.plan.code, token: buyState.token, config: cfg } })
      .then(function (d) {
        return api("/order/pay", { method: "POST", body: { order_id: d.order_id } }).then(function (p) {
          return { order_id: d.order_id, pay: p };
        });
      }).then(function (r) {
        if (r.pay.already) { toast(t("already_active")); go("accounts"); return; }
        var url = r.pay.pay_url;
        openPayUrl(url);                 // try to open directly…
        pollOrder(r.order_id, url);      // …and always show a link fallback + poll
      }).catch(function (e) {
        btn.disabled = false; btn.textContent = t("buy_pay");
        toast(e.code === "no_capacity" ? t("no_slots_now") : t("pay_failed"), true);
      });
  }
  // Open the OxaPay page directly (Telegram in-app browser, else a new tab).
  function openPayUrl(url) {
    if (!url) return;
    try { (tg && tg.openLink) ? tg.openLink(url) : window.open(url, "_blank"); }
    catch (e) { try { window.open(url, "_blank"); } catch (e2) {} }
  }
  // Copy to clipboard with a legacy fallback; toasts on success.
  function copyText(txt) {
    function legacy(s) {
      try {
        var ta = document.createElement("textarea"); ta.value = s; ta.setAttribute("readonly", "");
        ta.style.position = "absolute"; ta.style.left = "-9999px"; document.body.appendChild(ta);
        ta.select(); document.execCommand("copy"); document.body.removeChild(ta); toast(t("pay_copied"));
      } catch (e) {}
    }
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(txt).then(function () { toast(t("pay_copied")); }, function () { legacy(txt); });
      } else { legacy(txt); }
    } catch (e) { legacy(txt); }
  }
  function pollOrder(orderId, payUrl) {
    // No pay link → the buyer can't proceed; offer a way back instead of hanging.
    if (!payUrl) {
      view('<div class="empty">' + icon("alert", "empty-ic") + '<div class="empty-t">'+esc(t("pay_no_url"))+'</div>' +
        '<button class="btn primary" id="payBack" type="button">'+esc(t("pay_back"))+'</button></div>');
      var bb = el("payBack"); if (bb) bb.addEventListener("click", function () { go("buy"); });
      return;
    }
    view('<div class="empty">' + icon("wallet", "empty-ic") + '<div class="empty-t">'+esc(t("wait_pay_t"))+'</div>' +
      '<div class="empty-s">'+esc(t("wait_pay_s"))+'</div>' +
      '<div class="loader"><div class="spinner"></div></div>' +
      '<div class="pay-fallback">' +
        '<button class="btn primary" id="payOpen" type="button">'+esc(t("pay_open_btn"))+'</button>' +
        '<div class="hint">'+esc(t("pay_link_hint"))+'</div>' +
        '<div class="pay-url" id="payUrl">'+esc(payUrl)+'</div>' +
        '<button class="btn" id="payCopy" type="button">'+icon("copy")+' '+esc(t("pay_copy"))+'</button>' +
      '</div></div>');
    var ob = el("payOpen"); if (ob) ob.addEventListener("click", function () { openPayUrl(payUrl); });
    var cb = el("payCopy"); if (cb) cb.addEventListener("click", function () { copyText(payUrl); });
    var tries = 0, seq = renderSeq;
    var iv = setInterval(function () {
      if (stale(seq)) { clearInterval(iv); return; }
      tries++;
      api("/order/" + orderId).then(function (d) {
        if (d.status === "allocated") {
          clearInterval(iv); haptic("success");
          view('<div class="empty">' + icon("check", "empty-ic") + '<div class="empty-t">'+esc(t("sub_active_t"))+'</div>' +
            '<div class="empty-s">' + esc(d.stake_username || t("your_slot")) + ' ' + esc(t("sub_active_s")) + '</div>' +
            '<button class="btn primary" id="doneBtn" type="button">'+esc(t("view_subs"))+'</button></div>');
          el("doneBtn").addEventListener("click", function () { go("accounts"); });
        } else if (d.status === "failed" || d.status === "refunded" || d.status === "reservation_expired") {
          clearInterval(iv);
          toast(t("pay_not_done"), true); go("buy");
        }
      }).catch(function () {});
      if (tries > 120) clearInterval(iv);   // ~10 min
    }, 5000);
  }

  // ── Manage ───────────────────────────────────────────────────────────────
  function viewManage() {
    var seq = renderSeq; loading();
    api("/slots").then(function (r) {
      if (stale(seq)) return;
      var slots = (r && r.slots) || [];
      if (!slots.length) {
        view('<h2 class="sec">'+esc(t("nav_manage"))+'</h2><div class="empty"><div class="empty-t">'+esc(t("mng_none_t"))+'</div>' +
          '<div class="empty-s">'+esc(t("mng_none_s"))+'</div></div>');
        return;
      }
      view('<h2 class="sec">'+esc(t("manage_title"))+'</h2><div class="subs">' + slots.map(function (s) {
        var sl = secondLine(s);
        return '<button class="sub tap" data-id="' + s.slot_id + '" type="button">' +
          '<div class="sub-top"><div class="sub-name">' + esc(s.stake_username || "—") + '</div>' +
          '<span class="sub-status">' + (s.expired ? t("st_expired") : t("st_active")) + '</span>' + icon("chevron", "chev") + '</div>' +
          '<div class="sub-meta"><span class="tag">' + esc(s.plan || "") + '</span><span class="tag alt" data-exp="' + esc(s.expires_at || "") + '">' + esc(fmtExpiry(s.expires_at)) + '</span></div>' +
          reloadNote(s) +
          (sl ? '<div class="sub-exp" data-exp-abs="' + esc(s.expires_at || "") + '">' + esc(sl) + '</div>' : '') +
          '</button>';
      }).join("") + '</div>');
      startExpiryTick();
      Array.prototype.forEach.call(document.querySelectorAll(".sub.tap"), function (b) {
        var s = slots.filter(function (x) { return String(x.slot_id) === b.getAttribute("data-id"); })[0];
        b.addEventListener("click", function () { openManageSheet(s); });
      });
    }).catch(function () { if (!stale(seq)) fatal(t("err_slots")); });
  }
  function openManageSheet(s) {
    if (s.expired) { toast(t("slot_expired_toast"), true); return; }
    openSheet(s.stake_username || t("slot_word"), '<div class="card flat">' + cfgFields(s) +
      '<label class="lab sm">'+esc(t("mng_replace_key"))+'</label>' +
      '<input class="inp" id="cfgKey" type="password" placeholder="'+esc(t("mng_replace_ph"))+'" autocomplete="off">' +
      '<button class="btn primary" id="saveCfg" type="button">'+esc(t("save_changes"))+'</button></div>');
    wireToggles();
    el("saveCfg").addEventListener("click", function () {
      var body = readCfg();
      var nk = (el("cfgKey").value || "").trim();
      if (nk) body.stake_access_token = nk;
      var btn = el("saveCfg"); btn.disabled = true; btn.textContent = t("saving");
      api("/slots/" + s.slot_id + "/config", { method: "POST", body: body }).then(function () {
        closeSheet(); toast(t("saved")); go("manage");
      }).catch(function (e) {
        btn.disabled = false; btn.textContent = t("save_changes");
        toast(e.code && e.code.indexOf("verify") === 0 ? t("mng_verify_fail") : t("save_failed"), true);
      });
    });
  }

  // ── Stats ────────────────────────────────────────────────────────────────
  var statsType = "all";
  function viewStats() {
    renderStats("24h");
  }
  function renderStats(win) {
    var seq = renderSeq; loading();
    api("/stats?window=" + win + "&type=" + statsType).then(function (d) {
      if (stale(seq)) return;
      var earned = d.earned || {};
      var earnedRows = Object.keys(earned).length
        ? Object.keys(earned).map(function (c) { return '<span class="ecur">' + esc(c.toUpperCase()) + ' <b>' + esc(earned[c]) + '</b></span>'; }).join("")
        : '<span class="ecur muted">'+esc(t("no_earnings"))+'</span>';
      // Per-username breakdown (which Stake account claimed how much) — windowed.
      var byUser = d.earned_by_user || {};
      var byUserRows = Object.keys(byUser).map(function (u) {
        var curs = byUser[u];
        var amts = Object.keys(curs).map(function (c) { return esc(curs[c]) + " " + esc(c.toUpperCase()); }).join(", ");
        return '<div class="euser"><span class="euser-name">' + esc(u) + '</span><span class="euser-amt">' + amts + '</span></div>';
      }).join("");
      var windows = [["24h", t("win_24h")], ["7d", t("win_7d")], ["30d", t("win_30d")]];
      var tabs = windows.map(function (w) {
        return '<button class="stat-tab' + (w[0] === win ? " active" : "") + '" data-w="' + w[0] + '" type="button">' + esc(w[1]) + '</button>';
      }).join("");
      var types = [["all", t("type_all")], ["drop", t("type_drop")], ["reload", t("type_reload")]];
      var typeBtns = types.map(function (tp) {
        return '<button class="chip-btn' + (tp[0] === statsType ? " active" : "") + '" data-t="' + tp[0] + '" type="button">' + esc(tp[1]) + '</button>';
      }).join("");
      var recent = (d.recent_codes || []);
      var recentHtml = recent.length ? recent.map(function (r) {
        var ok = r.claimed;
        return '<div class="rc"><div class="rc-code">' +
          (r.username ? '<span class="rc-user">' + esc(r.username) + '</span> ' : '') + esc(r.code) + '</div>' +
          '<div class="rc-res ' + (ok ? "ok" : "no") + '">' + esc(ok ? t("claimed") : (r.result || "—")) + '</div>' +
          '<div class="rc-amt">' + (r.amount != null ? esc(r.amount) + " " + esc((r.currency || "").toUpperCase()) : "") + '</div></div>';
      }).join("") : '<div class="empty-s pad">'+esc(t("no_recent"))+'</div>';
      var truncNote = d.recent_truncated ? '<div class="rc-trunc">' + esc(t("recent_trunc")) + '</div>' : '';

      view('<h2 class="sec">'+esc(t("stats_title"))+'</h2>' +
        '<div class="stat-tabs">' + tabs + '</div>' +
        '<div class="chips">' + typeBtns + '</div>' +
        '<section class="earn"><div class="earn-lbl">'+esc(t("total_earned"))+'</div><div class="earn-vals">' + earnedRows + '</div>' +
        '<div class="earn-sub">' + esc(d.successful_claims || 0) + ' ' + esc(t("successful_claims")) + '</div>' +
        (byUserRows ? '<div class="euser-list">' + byUserRows + '</div>' : '') + '</section>' +
        '<h3 class="sec sm">'+esc(t("recent_codes"))+'</h3><div class="recent">' + recentHtml + '</div>' + truncNote);

      Array.prototype.forEach.call(document.querySelectorAll(".stat-tab"), function (b) {
        b.addEventListener("click", function () { renderStats(b.getAttribute("data-w")); });
      });
      Array.prototype.forEach.call(document.querySelectorAll(".chip-btn"), function (b) {
        b.addEventListener("click", function () { statsType = b.getAttribute("data-t"); renderStats(win); });
      });
    }).catch(function () { if (!stale(seq)) fatal(t("err_stats")); });
  }

  // ── Drop ─────────────────────────────────────────────────────────────────
  function viewDrop() {
    view('<h2 class="sec">'+esc(t("drop_title"))+'</h2>' +
      '<div class="note">' + icon("shield") +
      ' '+t("drop_note")+'</div>' +
      '<div class="card"><label class="lab">'+esc(t("drop_code"))+'</label>' +
      '<input class="inp" id="dropCode" type="text" placeholder="'+esc(t("drop_code_ph"))+'" maxlength="64" autocomplete="off">' +
      '<div class="hint">'+t("drop_hint1")+'</div>' +
      '<div class="tog" data-id="dropBonus" data-on="0"><span>'+esc(t("drop_bonus_label"))+'</span><span class="sw"><span class="knob"></span></span></div>' +
      '<div class="hint">'+t("drop_hint2")+'</div>' +
      '<button class="btn primary" id="dropBtn" type="button">'+esc(t("drop_btn"))+'</button></div>');
    wireToggles();
    el("dropBtn").addEventListener("click", function () {
      var code = (el("dropCode").value || "").trim();
      if (!code) { toast(t("enter_code"), true); return; }
      var bonus = document.querySelector('.tog[data-id="dropBonus"]').getAttribute("data-on") === "1";
      var btn = el("dropBtn"); btn.disabled = true; btn.textContent = t("dropping");
      api("/drop", { method: "POST", body: { code: code, couponType: bonus ? "bonus" : "drop" } }).then(function (d) {
        btn.disabled = false; btn.textContent = t("drop_btn");
        haptic("success");
        toast(d.slots ? (t("dropped_to") + " " + d.slots) : t("no_active_slots"));
        el("dropCode").value = "";
      }).catch(function (e) {
        btn.disabled = false; btn.textContent = "Drop to my slots";
        toast(e.code === "INVALID_CODE" ? t("invalid_code") : t("drop_failed"), true);
      });
    });
  }

  // ── boot ───────────────────────────────────────────────────────────────
  function boot() {
    try { if (tg) { tg.ready(); tg.expand(); } } catch (e) {}
    applyTheme();
    if (tg) { try { tg.onEvent("themeChanged", applyTheme); } catch (e) {} }
    var rb = el("refreshBtn"); if (rb) rb.addEventListener("click", function () { go(current); });
    initScrollCue();
    renderNav(); markNav();
    authenticate().then(function () {
      renderHeader(); go("accounts");
    }).catch(function (e) {
      renderHeader();
      fatal(e.message || "Please open inside Telegram.");
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
