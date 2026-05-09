"""WorkBridge Japan 영업용 Excel 파일 생성기.

생성: proposal/SAAS_PRICING_JP.xlsx

시트 구성:
  1. トップ        — 표지
  2. 見積シミュレーション — 점포 수 · 플랜 입력 시 자동 계산되는 ROI 계산기
  3. プラン比較    — 3 티어 정적 비교
  4. 5年TCO比較    — 자체 개발 vs SaaS 5년 총비용 비교 (수식)
  5. オプション    — 애드온 가격표
"""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.dimensions import ColumnDimension


# ============================================================
# 共通スタイル
# ============================================================

YEN_FMT = '"¥"#,##0;[Red]-"¥"#,##0'
INT_FMT = "#,##0"
PCT_FMT = "0%"

THIN = Side(style="thin", color="BFBFBF")
BORDER_ALL = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
THICK = Side(style="medium", color="333333")
BORDER_HEADER = Border(left=THIN, right=THIN, top=THICK, bottom=THICK)

H1 = Font(name="Yu Gothic", size=18, bold=True, color="1F2937")
H2 = Font(name="Yu Gothic", size=13, bold=True, color="1F2937")
H3 = Font(name="Yu Gothic", size=11, bold=True, color="374151")
BODY = Font(name="Yu Gothic", size=10, color="111827")
BODY_BOLD = Font(name="Yu Gothic", size=10, bold=True, color="111827")
SMALL = Font(name="Yu Gothic", size=9, color="6B7280")
INPUT_FONT = Font(name="Yu Gothic", size=11, bold=True, color="1E3A8A")

CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)
RIGHT = Alignment(horizontal="right", vertical="center")

FILL_HEAD = PatternFill("solid", fgColor="312E81")  # indigo-900
FILL_SUBHEAD = PatternFill("solid", fgColor="E0E7FF")  # indigo-100
FILL_INPUT = PatternFill("solid", fgColor="FEF3C7")  # amber-100
FILL_OUTPUT = PatternFill("solid", fgColor="D1FAE5")  # emerald-100
FILL_DANGER = PatternFill("solid", fgColor="FEE2E2")  # red-100
FILL_ALT = PatternFill("solid", fgColor="F9FAFB")
HEAD_FONT = Font(name="Yu Gothic", size=11, bold=True, color="FFFFFF")


def _set_widths(ws, widths):
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _heading(ws, row, col, text, font=H1):
    ws.cell(row=row, column=col, value=text).font = font


def _cell(ws, row, col, value, *, font=BODY, fill=None, align=None, fmt=None, border=BORDER_ALL):
    c = ws.cell(row=row, column=col, value=value)
    c.font = font
    if fill is not None:
        c.fill = fill
    if align is not None:
        c.alignment = align
    if fmt is not None:
        c.number_format = fmt
    if border is not None:
        c.border = border
    return c


# ============================================================
# 1. トップ (cover)
# ============================================================

def build_cover(wb: Workbook) -> None:
    ws = wb.active
    ws.title = "トップ"
    _set_widths(ws, [4, 30, 30, 30, 30, 4])

    ws["B2"] = "WorkBridge Japan"
    ws["B2"].font = Font(name="Yu Gothic", size=24, bold=True, color="312E81")
    ws.merge_cells("B2:E2")

    ws["B3"] = "料金見積・ROI シミュレーション (税抜)"
    ws["B3"].font = Font(name="Yu Gothic", size=13, color="4B5563")
    ws.merge_cells("B3:E3")

    ws["B5"] = "本ファイルは営業・お客様への料金提示・社内見積検討に使用するワーク\n" \
              "シートです。「見積シミュレーション」シートに店舗数・プラン等を入力\n" \
              "すると、自動的に月額・年額・5 年総コストおよび自社開発との比較が\n" \
              "計算されます。"
    ws["B5"].font = BODY
    ws["B5"].alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
    ws.merge_cells("B5:E8")
    ws.row_dimensions[5].height = 18
    ws.row_dimensions[6].height = 18
    ws.row_dimensions[7].height = 18
    ws.row_dimensions[8].height = 18

    ws["B10"] = "シート構成"
    ws["B10"].font = H2

    sheets_desc = [
        ("見積シミュレーション", "店舗数・プラン入力で月額/年額/5 年総コストを自動計算"),
        ("プラン比較", "Starter / Business / Enterprise の機能・SLA・サポート比較表"),
        ("5年TCO比較", "自社開発 (中堅 IT / 大手 SIer) vs WorkBridge SaaS の 5 年総額比較"),
        ("オプション", "追加で発生する可能性のある費用項目とアドオン料金"),
    ]
    for i, (name, desc) in enumerate(sheets_desc):
        r = 11 + i
        _cell(ws, r, 2, name, font=BODY_BOLD, fill=FILL_SUBHEAD, align=LEFT)
        _cell(ws, r, 3, desc, font=BODY, align=LEFT)
        ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=5)

    ws["B16"] = "ご使用にあたって"
    ws["B16"].font = H2

    notes = [
        "• 黄色セル (FEF3C7) は入力項目です。値を変更すると下記が自動更新されます。",
        "• 緑色セル (D1FAE5) は自動計算結果です。直接編集しないでください。",
        "• 表記は全て税抜です。実際の見積もりには税額・送金手数料等を別途加算してください。",
        "• 想定為替: ¥150/$。為替変動が大きい場合、「5年TCO比較」シートの定義値を調整してください。",
        "• Google Translate API 単価: $20/100万字 (2026年時点)。GCP 値上げに応じて調整可能。",
    ]
    for i, line in enumerate(notes):
        ws.cell(row=17 + i, column=2, value=line).font = SMALL
        ws.merge_cells(start_row=17 + i, start_column=2, end_row=17 + i, end_column=5)

    ws["B24"] = "提案先："
    ws["B24"].font = BODY_BOLD
    _cell(ws, 24, 3, "[貴社名]", font=INPUT_FONT, fill=FILL_INPUT, align=LEFT)
    ws.merge_cells("C24:E24")

    ws["B25"] = "提案者："
    ws["B25"].font = BODY_BOLD
    _cell(ws, 25, 3, "[当社名]", font=INPUT_FONT, fill=FILL_INPUT, align=LEFT)
    ws.merge_cells("C25:E25")

    ws["B26"] = "作成日："
    ws["B26"].font = BODY_BOLD
    _cell(ws, 26, 3, "[YYYY年MM月DD日]", font=INPUT_FONT, fill=FILL_INPUT, align=LEFT)
    ws.merge_cells("C26:E26")


# ============================================================
# 2. 見積シミュレーション
# ============================================================

def build_calculator(wb: Workbook) -> None:
    ws = wb.create_sheet("見積シミュレーション")
    _set_widths(ws, [4, 24, 20, 18, 18, 18, 18, 4])

    _heading(ws, 2, 2, "見積シミュレーション", font=H1)
    _cell(ws, 3, 2, "黄色セルに値を入力すると、緑色セルが自動計算されます。",
          font=SMALL, align=LEFT, border=None)
    ws.merge_cells("B3:G3")

    # === 入力 ===
    _heading(ws, 5, 2, "入力項目", font=H2)

    inputs = [
        ("店舗数", 10, "INT_FMT", "ご利用予定の店舗数"),
        ("選択プラン", "Business", "TEXT", "Starter / Business / Enterprise"),
        ("Starter 月額/店舗", 12800, "YEN_FMT", "1 店舗のみ"),
        ("Business 月額/店舗", 9800, "YEN_FMT", "1〜5 店舗向け"),
        ("Enterprise 月額/店舗", 6800, "YEN_FMT", "10 店舗以上 + 本部ライセンス"),
        ("Enterprise 本部ライセンス/月", 100000, "YEN_FMT", "Enterprise のみ"),
        ("初期費用 (Starter)", 150000, "YEN_FMT", "一括"),
        ("初期費用 (Business)", 300000, "YEN_FMT", "一括"),
        ("初期費用 (Enterprise)", 800000, "YEN_FMT", "規模により協議"),
        ("年間契約割引 (%)", 0.10, "PCT_FMT", "10〜20% (Enterprise 年契約時)"),
        ("契約年数", 1, "INT_FMT", "ROI 計算で 1〜5 年"),
    ]

    for i, (label, val, fmt_key, hint) in enumerate(inputs):
        r = 6 + i
        _cell(ws, r, 2, label, font=BODY_BOLD, fill=FILL_SUBHEAD, align=LEFT)
        fmt_map = {"INT_FMT": INT_FMT, "YEN_FMT": YEN_FMT, "PCT_FMT": PCT_FMT, "TEXT": "@"}
        cell = _cell(ws, r, 3, val, font=INPUT_FONT, fill=FILL_INPUT,
                     align=RIGHT if fmt_key != "TEXT" else CENTER, fmt=fmt_map[fmt_key])
        _cell(ws, r, 4, hint, font=SMALL, align=LEFT, border=None)
        ws.merge_cells(start_row=r, start_column=4, end_row=r, end_column=7)

    # 名前定義 (수식에서 사용)
    NAMES = {
        "store_count": "C6",
        "plan_choice": "C7",
        "price_starter": "C8",
        "price_business": "C9",
        "price_enterprise": "C10",
        "hq_license": "C11",
        "init_starter": "C12",
        "init_business": "C13",
        "init_enterprise": "C14",
        "annual_discount": "C15",
        "contract_years": "C16",
    }
    for name, ref in NAMES.items():
        wb.defined_names[name] = DefinedName(name, attr_text=f"見積シミュレーション!${ref[0]}${ref[1:]}")

    # === 자동 계산 ===
    out_start = 19
    _heading(ws, out_start, 2, "自動計算結果", font=H2)

    # プランごとの月額単価選択
    pick_unit = (
        f'=IF($C$7="Starter",$C$8,'
        f'IF($C$7="Business",$C$9,'
        f'IF($C$7="Enterprise",$C$10,$C$9)))'
    )
    pick_init = (
        f'=IF($C$7="Starter",$C$12,'
        f'IF($C$7="Business",$C$13,'
        f'IF($C$7="Enterprise",$C$14,$C$13)))'
    )
    pick_hq = f'=IF($C$7="Enterprise",$C$11,0)'

    outs = [
        ("選択プラン単価/店舗/月", pick_unit, "YEN_FMT"),
        ("店舗数 × 単価/月 (税抜)", "=$C$6*C20", "YEN_FMT"),
        ("本部ライセンス/月 (Enterprise のみ)", pick_hq, "YEN_FMT"),
        ("月額合計 (税抜)", "=C21+C22", "YEN_FMT"),
        ("年間契約割引額/月", "=C23*$C$15", "YEN_FMT"),
        ("月額 (年間契約割引適用後)", "=C23-C24", "YEN_FMT"),
        ("年額 (税抜・割引後)", "=C25*12", "YEN_FMT"),
        ("初期費用 (一括)", pick_init, "YEN_FMT"),
        ("契約年数の総額 (税抜)", "=C26*$C$16+C27", "YEN_FMT"),
    ]
    for i, (label, formula, fmt_key) in enumerate(outs):
        r = out_start + 1 + i
        _cell(ws, r, 2, label, font=BODY_BOLD, fill=FILL_SUBHEAD, align=LEFT)
        fill = FILL_OUTPUT if "総額" in label or "月額" in label or "年額" in label else None
        c = _cell(ws, r, 3, formula, font=BODY_BOLD if fill else BODY,
                  fill=fill, align=RIGHT, fmt=YEN_FMT)
    # 強調: 主要指標
    ws["C25"].font = Font(name="Yu Gothic", size=12, bold=True, color="047857")
    ws["C26"].font = Font(name="Yu Gothic", size=12, bold=True, color="047857")
    ws["C28"].font = Font(name="Yu Gothic", size=13, bold=True, color="047857")

    # === 注意 ===
    note_row = out_start + len(outs) + 2
    ws.cell(row=note_row, column=2,
            value="※ 上記は基準価格の自動見積もりです。実際の最終価格は規模・要件・契約条件に応じ個別協議となります。"
            ).font = SMALL
    ws.merge_cells(start_row=note_row, start_column=2, end_row=note_row, end_column=7)


# ============================================================
# 3. プラン比較
# ============================================================

def build_plan_comparison(wb: Workbook) -> None:
    ws = wb.create_sheet("プラン比較")
    _set_widths(ws, [4, 32, 24, 24, 28, 4])

    _heading(ws, 2, 2, "プラン別 機能・サービス比較", font=H1)

    rows = [
        ("項目", "Starter", "Business", "Enterprise"),  # ヘッダ
        ("月額/店舗 (税抜)", "¥12,800", "¥9,800", "¥6,800〜"),
        ("本部ライセンス/月", "─", "─", "¥80,000〜¥150,000"),
        ("初期費用", "¥150,000", "¥300,000", "¥0〜¥2,000,000 (規模協議)"),
        ("最低契約期間", "3 ヶ月", "6 ヶ月", "12 ヶ月 (年間契約割引 10〜20%)"),
        ("対象店舗数", "単独店舗のみ", "1〜5 店舗", "10 店舗以上"),
        ("スタッフ登録/店舗", "〜10 名", "〜20 名", "無制限"),
        ("月間翻訳 API 字数", "50 万字", "200 万字/店舗", "Fair Use 無制限"),
        ("超過課金", "¥1,000/10 万字", "¥800/10 万字", "─"),
        ("リアルタイム指示送受信", "✓", "✓", "✓"),
        ("6 言語自動翻訳", "✓", "✓", "✓"),
        ("やさしい日本語変換", "✓", "✓", "✓"),
        ("3 ボタン応答 + カスタム音声応答", "✓", "✓", "✓"),
        ("画像添付指示", "✓", "✓", "✓"),
        ("CSV 一括登録", "✓", "✓", "✓"),
        ("60 日分の履歴保持", "✓", "✓", "選択により最大 12 ヶ月"),
        ("PWA (iOS/Android 対応)", "✓", "✓", "✓"),
        ("店舗専用業務用語辞書", "─", "✓", "✓"),
        ("表現・言い換え辞書", "─", "✓", "✓"),
        ("店舗別ロゴ・ブランド統合", "─", "オプション", "標準"),
        ("本部一括ダッシュボード", "─", "─", "✓"),
        ("複数店舗一斉指示", "─", "─", "✓"),
        ("店舗別 売上・稼働分析", "─", "─", "✓"),
        ("SSO (SAML/OIDC)", "─", "─", "✓"),
        ("監査ログ", "─", "─", "✓"),
        ("DB 行レベル分離 (Postgres RLS)", "─", "─", "✓"),
        ("メールサポート", "✓", "✓", "✓"),
        ("チャットサポート", "─", "✓", "✓"),
        ("電話サポート", "─", "─", "✓"),
        ("専任カスタマーサクセス", "─", "─", "✓"),
        ("応答時間 (営業時間内)", "4 時間", "2 時間", "1 時間 (重大障害 24h)"),
        ("稼働率目標 (SLA)", "ベストエフォート", "99.5%", "99.9% + 障害時クレジット"),
        ("バックアップ", "日次/7 日", "日次/14 日", "日次/30 日 + オンデマンド"),
        ("オンライントレーニング", "1 回 (1 時間)", "2 回 (各 1 時間)", "無制限"),
        ("オンサイトトレーニング", "別途有料", "別途有料", "1 日含む"),
        ("ペネトレーションテスト", "─", "─", "年 1 回含む"),
        ("データ削除 SLA", "5 営業日", "5 営業日", "2 営業日"),
        ("DPA / 個人情報チェックシート", "標準", "標準 + カスタム項目", "個別協議 + DPIA"),
        ("ホスティング", "日本 (東京)", "日本 (東京)", "日本 (東京) + 専用環境オプション"),
    ]

    start_row = 4
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            r, c = start_row + i, 2 + j
            if i == 0:
                cell = _cell(ws, r, c, val, font=HEAD_FONT, fill=FILL_HEAD, align=CENTER)
            else:
                fill = FILL_ALT if i % 2 == 1 else None
                font = BODY_BOLD if j == 0 else BODY
                align = LEFT if j == 0 else CENTER
                _cell(ws, r, c, val, font=font, fill=fill, align=align)
        ws.row_dimensions[start_row + i].height = 22

    ws.row_dimensions[start_row].height = 28
    ws.freeze_panes = ws.cell(row=start_row + 1, column=3)


# ============================================================
# 4. 5 年 TCO 比較
# ============================================================

def build_tco(wb: Workbook) -> None:
    ws = wb.create_sheet("5年TCO比較")
    _set_widths(ws, [4, 32, 22, 22, 22, 4])

    _heading(ws, 2, 2, "自社開発 vs WorkBridge SaaS — 5 年総コスト比較", font=H1)

    _cell(ws, 4, 2, "前提: 50 店舗チェーン、5 年運用",
          font=BODY_BOLD, align=LEFT, border=None)
    ws.merge_cells("B4:E4")

    # === 입력 (사용자 변경 가능) ===
    _heading(ws, 6, 2, "前提条件 (調整可)", font=H2)

    presets = [
        ("店舗数", 50, INT_FMT),
        ("運用年数", 5, INT_FMT),
        ("中堅 IT 開発初期費 (¥)", 55_000_000, YEN_FMT),
        ("中堅 IT 年間運用費 (¥)", 6_000_000, YEN_FMT),
        ("大手 SIer 開発初期費 (¥)", 150_000_000, YEN_FMT),
        ("大手 SIer 年間運用費 (¥)", 15_000_000, YEN_FMT),
        ("WorkBridge Enterprise 月額/店舗 (¥)", 6800, YEN_FMT),
        ("WorkBridge Enterprise 本部/月 (¥)", 100000, YEN_FMT),
        ("WorkBridge 初期費 (¥)", 800000, YEN_FMT),
    ]
    for i, (label, val, fmt) in enumerate(presets):
        r = 7 + i
        _cell(ws, r, 2, label, font=BODY_BOLD, fill=FILL_SUBHEAD, align=LEFT)
        _cell(ws, r, 3, val, font=INPUT_FONT, fill=FILL_INPUT, align=RIGHT, fmt=fmt)

    # === 計算 ===
    _heading(ws, 17, 2, "5 年総コスト比較", font=H2)

    headers = ("形態", "初期費用", "年間運用費 × N", "5 年総額")
    for j, h in enumerate(headers):
        _cell(ws, 18, 2 + j, h, font=HEAD_FONT, fill=FILL_HEAD, align=CENTER)
    ws.row_dimensions[18].height = 26

    # 中堅 IT
    _cell(ws, 19, 2, "自社開発 (中堅 IT 会社)", font=BODY_BOLD, align=LEFT)
    _cell(ws, 19, 3, "=C9", font=BODY, align=RIGHT, fmt=YEN_FMT)
    _cell(ws, 19, 4, "=C10*C8", font=BODY, align=RIGHT, fmt=YEN_FMT)
    _cell(ws, 19, 5, "=C19+D19", font=BODY_BOLD, fill=FILL_DANGER, align=RIGHT, fmt=YEN_FMT)

    # 大手 SI
    _cell(ws, 20, 2, "自社開発 (大手 SIer)", font=BODY_BOLD, align=LEFT)
    _cell(ws, 20, 3, "=C11", font=BODY, align=RIGHT, fmt=YEN_FMT)
    _cell(ws, 20, 4, "=C12*C8", font=BODY, align=RIGHT, fmt=YEN_FMT)
    _cell(ws, 20, 5, "=C20+D20", font=BODY_BOLD, fill=FILL_DANGER, align=RIGHT, fmt=YEN_FMT)

    # WorkBridge SaaS
    _cell(ws, 21, 2, "WorkBridge SaaS (Enterprise)", font=BODY_BOLD, fill=FILL_OUTPUT, align=LEFT)
    _cell(ws, 21, 3, "=C15", font=BODY, fill=FILL_OUTPUT, align=RIGHT, fmt=YEN_FMT)
    _cell(ws, 21, 4, "=(C13*C7+C14)*12*C8", font=BODY, fill=FILL_OUTPUT, align=RIGHT, fmt=YEN_FMT)
    _cell(ws, 21, 5, "=C21+D21", font=Font(name="Yu Gothic", size=12, bold=True, color="047857"),
          fill=FILL_OUTPUT, align=RIGHT, fmt=YEN_FMT)

    # 倍率
    _heading(ws, 23, 2, "コスト比較", font=H2)
    _cell(ws, 24, 2, "中堅 IT 開発 / WorkBridge SaaS", font=BODY_BOLD, align=LEFT)
    _cell(ws, 24, 3, "=E19/E21", font=BODY_BOLD, fill=FILL_OUTPUT, align=RIGHT, fmt='0.00"倍"')
    _cell(ws, 25, 2, "大手 SIer 開発 / WorkBridge SaaS", font=BODY_BOLD, align=LEFT)
    _cell(ws, 25, 3, "=E20/E21", font=BODY_BOLD, fill=FILL_OUTPUT, align=RIGHT, fmt='0.00"倍"')

    # メモ
    _cell(ws, 27, 2,
          "※ 中堅 IT 開発の年間運用費は、サーバ運用・保守エンジニア 1〜2 名分を想定。\n"
          "※ 大手 SIer は運用も SLA 付き保守契約での想定。\n"
          "※ WorkBridge SaaS の年間運用費には、機能改善・セキュリティアップデート・サポート全て含む。",
          font=SMALL, align=LEFT, border=None)
    ws.merge_cells("B27:E29")
    ws.row_dimensions[27].height = 16
    ws.row_dimensions[28].height = 16
    ws.row_dimensions[29].height = 16


# ============================================================
# 5. オプション
# ============================================================

def build_options(wb: Workbook) -> None:
    ws = wb.create_sheet("オプション")
    _set_widths(ws, [4, 44, 28, 38, 4])

    _heading(ws, 2, 2, "オプション・アドオン (全プラン共通)", font=H1)

    headers = ("項目", "料金 (税抜)", "備考")
    for j, h in enumerate(headers):
        _cell(ws, 4, 2 + j, h, font=HEAD_FONT, fill=FILL_HEAD, align=CENTER)
    ws.row_dimensions[4].height = 26

    options = [
        ("標準 6 言語以外の言語追加 (タイ語・タガログ語等)",
         "初期 ¥100,000 + ¥3,000/月", "翻訳精度評価込み"),
        ("カスタム機能開発",
         "¥500,000〜", "規模・要件に応じ個別見積"),
        ("POS / 勤怠管理 / シフト管理連携",
         "¥300,000〜¥2,000,000", "対象システムにより変動"),
        ("専用ロゴ・ブランド統合",
         "¥150,000 (一括)", "管理者・スタッフ画面"),
        ("オンサイトサポート (店舗訪問)",
         "¥150,000/日", "交通費別"),
        ("プレミアム SLA (24 時間緊急対応)",
         "¥150,000/月", "Enterprise の標準オプション"),
        ("月次定例ミーティング・改善レポート",
         "¥50,000/月", "Enterprise 既定"),
        ("データ保持期間延長 (60 日 → 6 ヶ月)",
         "¥10,000/月", ""),
        ("データ保持期間延長 (60 日 → 12 ヶ月)",
         "¥20,000/月", ""),
        ("ペネトレーションテスト (年 1 回)",
         "¥500,000〜¥1,500,000/年", "Enterprise 既定"),
        ("DPIA (データ保護影響評価) 個別実施",
         "¥300,000〜", ""),
        ("管理者向けスタッフ教育コンテンツ作成",
         "¥200,000〜", "業種別カリキュラム作成"),
    ]

    for i, (item, price, note) in enumerate(options):
        r = 5 + i
        fill = FILL_ALT if i % 2 == 0 else None
        _cell(ws, r, 2, item, font=BODY, fill=fill, align=LEFT)
        _cell(ws, r, 3, price, font=BODY_BOLD, fill=fill, align=CENTER)
        _cell(ws, r, 4, note, font=SMALL, fill=fill, align=LEFT)
        ws.row_dimensions[r].height = 22

    end_row = 5 + len(options) + 2
    _cell(ws, end_row, 2,
          "※ 上記オプションは事前見積もりとお客様の承諾の上で実施いたします。\n"
          "※ 既存契約の途中追加も可能です。次回請求月から適用されます。",
          font=SMALL, align=LEFT, border=None)
    ws.merge_cells(start_row=end_row, start_column=2, end_row=end_row, end_column=4)


# ============================================================
# Main
# ============================================================

def main():
    wb = Workbook()
    build_cover(wb)
    build_calculator(wb)
    build_plan_comparison(wb)
    build_tco(wb)
    build_options(wb)

    out = Path(__file__).resolve().parent / "SAAS_PRICING_JP.xlsx"
    wb.save(out)
    print(f"saved: {out}")


if __name__ == "__main__":
    main()
