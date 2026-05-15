"""영업 데모용 시드 스크립트.

한 번 실행하면 PoPo / COCO 두 대리점 + 산하 워크스페이스 4 개 +
각 워크스페이스 스탭 3~5 명 + 데모 지시 이력 까지 셋업 완료.

사용법:
    PYTHONIOENCODING=utf-8 python scripts/seed_demo.py
    PYTHONIOENCODING=utf-8 python scripts/seed_demo.py --reset    # 시드 깨끗이 다시
    PYTHONIOENCODING=utf-8 python scripts/seed_demo.py --dry-run  # 무엇이 만들어질지 미리보기

영향 범위:
- 「demo-」 또는 「popo」/「coco」 슬러그의 distributors / workspaces 만 건드림
- c-direct (시스템 가상 대리점) / 운영 데이터는 절대 안 건드림

결제 (Lemon Squeezy) 는 연동하지 않음. 본인이 영업 미팅에서
실제 결제 흐름을 시연하려면 별도로 「Lemon 대시보드 → Test mode」 사용.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid

# 프로젝트 루트 임포트
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from app.db.sqlite import init_db, get_connection  # noqa: E402


# ============================================================
# Seed plan
# ============================================================

DISTRIBUTORS = [
    {
        "slug": "popo",
        "name": "PoPo 株式会社",
        "contact_person": "田中 太郎",
        "contact_phone": "03-1234-5678",
        "contact_email": "tanaka@popo.co.jp",
        "owner_email": "popo-admin@popo.co.jp",
        "owner_password": "PopoDemo2026!",
        "wholesale_starter": 8000,
        "wholesale_business": 6500,
        "wholesale_enterprise": 4500,  # 50점포 돌파 → 볼륨 디스카운트 예시
        "wholesale_mvp_fee": 5000000,
    },
    {
        "slug": "coco",
        "name": "COCO ジャパン",
        "contact_person": "山田 花子",
        "contact_phone": "045-987-6543",
        "contact_email": "yamada@coco.co.jp",
        "owner_email": "coco-admin@coco.co.jp",
        "owner_password": "CocoDemo2026!",
        "wholesale_starter": 8000,
        "wholesale_business": 6500,
        "wholesale_enterprise": 5000,
        "wholesale_mvp_fee": 5000000,
    },
]

WORKSPACES = [
    {
        "dist_slug": "popo",
        "slug": "abcramen",
        "name": "ABCラーメン 関町店",
        "company_name": "株式会社 ABC ラーメン",
        "plan": "enterprise",
        "owner_password": "AbcDemo2026!",
        "retail_price_starter": 15800,
        "retail_price_business": 12800,
        "retail_price_enterprise": 9800,
        "logo_url": None,  # 로고는 데모에서는 letter-avatar fallback
        "staff": [
            ("tamura", "田村 拓海", "tamuraPw!"),
            ("iroen", "이로엔", "iroenPw!"),
            ("tran", "Trần Văn Minh", "tranPw!"),
            ("wang", "王 强", "wangPw!"),
            ("kim", "김민지", "kimPw!"),
        ],
        "demo_instructions": [
            ("レンジで麺を温め直す前に、必ず氷を取ってください。", "OK"),
            ("お客様にエプロンを必ず提供してください。", "OK"),
            ("チャーシュー切る角度、もう少し斜めに。", "NG"),  # 못 알아들음 — 교재 후보
            ("テーブル番号3、追加の餃子オーダー", "REPEAT"),
        ],
    },
    {
        "dist_slug": "popo",
        "slug": "umaicurry",
        "name": "UmaiCurry 渋谷店",
        "company_name": "株式会社 UmaiCurry",
        "plan": "business",
        "owner_password": "UmaiDemo2026!",
        "retail_price_starter": 15800,
        "retail_price_business": 12800,
        "retail_price_enterprise": 11000,
        "logo_url": None,
        "staff": [
            ("sato", "佐藤 健", "satoPw!"),
            ("lee", "이지수", "leePw!"),
            ("nguyen", "Nguyễn Lan", "nguyenPw!"),
        ],
        "demo_instructions": [
            ("カレーのトッピング、チーズは追加 200 円", "OK"),
            ("辛さレベル、お客様の希望を確認してください", "OK"),
        ],
    },
    {
        "dist_slug": "popo",
        "slug": "defshokudo",
        "name": "DEF食堂 新宿店",
        "company_name": "株式会社 DEF",
        "plan": "starter",
        "owner_password": "DefDemo2026!",
        "retail_price_starter": 15800,
        "retail_price_business": None,
        "retail_price_enterprise": None,
        "logo_url": None,
        "staff": [
            ("yoshida", "吉田 美咲", "yoshidaPw!"),
            ("park", "박서연", "parkPw!"),
        ],
        "demo_instructions": [
            ("ランチタイムのライス大盛り、無料サービスです", "OK"),
        ],
    },
    {
        "dist_slug": "coco",
        "slug": "ghidining",
        "name": "GHIダイニング 横浜店",
        "company_name": "株式会社 GHI ダイニング",
        "plan": "enterprise",
        "owner_password": "GhiDemo2026!",
        "retail_price_starter": 15800,
        "retail_price_business": 12800,
        "retail_price_enterprise": 9800,
        "logo_url": None,
        "staff": [
            ("suzuki", "鈴木 一郎", "suzukiPw!"),
            ("choi", "최영민", "choiPw!"),
            ("kumar", "Raj Kumar", "kumarPw!"),
            ("li", "李 明", "liPw!"),
        ],
        "demo_instructions": [
            ("VIPテーブルのワインリスト、必ず再確認", "OK"),
            ("コース料理の説明、お客様の母国語で対応してください", "NG"),  # 교재 후보
        ],
    },
]


# ============================================================
# Helpers
# ============================================================


def _row_val(row, key):
    """sqlite3.Row / _HybridRow 호환 헬퍼."""
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _existing_dist_by_slug(slug):
    conn = get_connection()
    return conn.execute("SELECT id FROM distributors WHERE slug = ?", (slug,)).fetchone()


def _existing_ws_by_dist_and_slug(distributor_id, slug):
    conn = get_connection()
    return conn.execute(
        "SELECT id FROM workspaces WHERE distributor_id = ? AND slug = ?",
        (distributor_id, slug),
    ).fetchone()


def _reset_demo() -> dict:
    """슬러그가 시드 plan 에 있는 dist + ws 만 정리. 다른 데이터는 안 건드림."""
    from app.services.distributors import distributors as dstore

    counts = {"distributors": 0, "workspaces": 0, "files_deleted": 0}
    for d_plan in DISTRIBUTORS:
        row = _existing_dist_by_slug(d_plan["slug"])
        if row is None:
            continue
        d_id = _row_val(row, "id") or _row_val(row, 0)
        try:
            sub = dstore.delete_with_cascade(d_id)
            counts["distributors"] += int(sub.get("distributors", 0) or 0)
            counts["workspaces"] += int(sub.get("workspaces", 0) or 0)
            counts["files_deleted"] += int(sub.get("files_deleted", 0) or 0)
        except Exception as e:
            print(f"  ! reset failed for {d_plan['slug']}: {e}")
    return counts


def _seed_distributor(plan, dry_run: bool) -> "Distributor | None":  # type: ignore[name-defined]
    from app.services.distributors import distributors as dstore

    existing = _existing_dist_by_slug(plan["slug"])
    if existing is not None:
        d_id = _row_val(existing, "id") or _row_val(existing, 0)
        d = dstore.get(d_id)
        print(f"  ✓ distributor 「{plan['slug']}」 already exists (id={d.id[:8]}...)")
        return d

    if dry_run:
        print(f"  + would create distributor: {plan['slug']}  ({plan['name']})")
        return None

    d = dstore.create(
        slug=plan["slug"],
        name=plan["name"],
        owner_email=plan["owner_email"],
        owner_password=plan["owner_password"],
        contact_person=plan["contact_person"],
        contact_phone=plan["contact_phone"],
        contact_email=plan["contact_email"],
        wholesale_starter=plan["wholesale_starter"],
        wholesale_business=plan["wholesale_business"],
        wholesale_enterprise=plan["wholesale_enterprise"],
        wholesale_mvp_fee=plan["wholesale_mvp_fee"],
    )
    print(f"  + created distributor: {d.slug}  (id={d.id[:8]}...)")
    return d


def _seed_workspace(ws_plan, dist_id_by_slug, dry_run: bool) -> "Workspace | None":  # type: ignore[name-defined]
    from app.services.distributors import hash_password
    from app.services.stores import workspaces as wstore

    d_id = dist_id_by_slug.get(ws_plan["dist_slug"])
    if not d_id:
        print(f"  ! workspace 「{ws_plan['slug']}」 의 dist 「{ws_plan['dist_slug']}」 가 없음")
        return None

    existing = _existing_ws_by_dist_and_slug(d_id, ws_plan["slug"])
    if existing is not None:
        ws_id = _row_val(existing, "id") or _row_val(existing, 0)
        ws = wstore.get(ws_id)
        print(f"    ✓ workspace 「{ws.slug}」 already exists (id={ws.id[:8]}...)")
        return ws

    if dry_run:
        print(f"    + would create workspace: {ws_plan['dist_slug']}/{ws_plan['slug']}  (plan={ws_plan['plan']})")
        return None

    try:
        ws = wstore.create(
            ws_plan["name"],
            distributor_id=d_id,
            slug=ws_plan["slug"],
            owner_password_hash=hash_password(ws_plan["owner_password"]),
            company_name=ws_plan.get("company_name", ""),
            logo_url=ws_plan.get("logo_url"),
            retail_price_starter=ws_plan.get("retail_price_starter"),
            retail_price_business=ws_plan.get("retail_price_business"),
            retail_price_enterprise=ws_plan.get("retail_price_enterprise"),
            assigned_plan=ws_plan["plan"],
        )
        print(f"    + created workspace: {ws_plan['dist_slug']}/{ws.slug}  (plan={ws.assigned_plan})")
        return ws
    except Exception as e:
        print(f"    ! workspace create failed: {e}")
        return None


def _seed_staff(ws_id, staff_plans, dry_run: bool) -> list[tuple[str, str]]:
    """등록된 스탭 (id, login, label) 목록 반환 (지시 시뮬레이션용)."""
    from app.services.staff_accounts import staff_accounts as sastore

    created = []
    for (login_id, display_name, password) in staff_plans:
        # 이미 있으면 가져오기
        existing = sastore.get_by_workspace_login(ws_id, login_id)
        if existing is not None:
            print(f"      ✓ staff 「{login_id}」 ({display_name}) already exists")
            created.append((existing.id, login_id, display_name))
            continue
        if dry_run:
            print(f"      + would create staff: {login_id} ({display_name})")
            continue
        try:
            acc = sastore.create(
                workspace_id=ws_id,
                login_id=login_id,
                display_name=display_name,
                plain_password=password,
            )
            created.append((acc.id, login_id, display_name))
            print(f"      + staff: {login_id} ({display_name})")
        except Exception as e:
            print(f"      ! staff create failed for {login_id}: {e}")
    return created


def _seed_instructions(ws_id, staff_list, ws_plan, dry_run: bool) -> int:
    """데모용 지시·응답 시드. staff_list 에서 응답자 추출."""
    from app.services.instruction_history import create_round, record_reply

    if not staff_list:
        return 0
    instructions = ws_plan.get("demo_instructions") or []
    if not instructions:
        return 0
    if dry_run:
        print(f"      + would create {len(instructions)} demo instructions")
        return 0

    count = 0
    for (text, button) in instructions:
        recipients = [
            {"token": f"demo-token-{uuid.uuid4().hex[:8]}", "label": label, "staff_account_id": sid}
            for (sid, _login, label) in staff_list
        ]
        try:
            rid = create_round(
                workspace_id=ws_id,
                text=text,
                mode="broadcast",
                recipients=recipients,
            )
            # 첫 2 명만 응답하도록 시뮬레이션 (현실감)
            for r in recipients[:2]:
                record_reply(
                    workspace_id=ws_id,
                    instruction_id=rid,
                    worker_token=r["token"],
                    worker_label=r["label"],
                    staff_account_id=r["staff_account_id"],
                    button=button,
                )
            count += 1
        except Exception as e:
            print(f"      ! instruction create failed: {e}")
    print(f"      + {count} demo instructions sent with replies")
    return count


# ============================================================
# Main
# ============================================================


def main() -> int:
    parser = argparse.ArgumentParser(description="LinguaSync 영업 데모 시드 스크립트")
    parser.add_argument("--reset", action="store_true", help="기존 시드 데이터 삭제 후 다시 생성")
    parser.add_argument("--dry-run", action="store_true", help="DB 변경 없이 출력만")
    args = parser.parse_args()

    init_db()

    if args.reset:
        print("== Reset: deleting existing demo data ==")
        counts = _reset_demo()
        print(f"  removed: distributors={counts['distributors']}  workspaces={counts['workspaces']}  files={counts['files_deleted']}")
        print()

    if args.dry_run:
        print("== DRY RUN — no DB changes ==\n")

    print("== Seeding distributors ==")
    dist_id_by_slug: dict[str, str] = {}
    for plan in DISTRIBUTORS:
        d = _seed_distributor(plan, dry_run=args.dry_run)
        if d is not None:
            dist_id_by_slug[plan["slug"]] = d.id

    print()
    print("== Seeding workspaces + staff + demo instructions ==")
    total_ws = 0
    total_staff = 0
    total_instr = 0
    ws_summary: list[dict] = []
    for ws_plan in WORKSPACES:
        print(f"\n  workspace: {ws_plan['dist_slug']}/{ws_plan['slug']}")
        ws = _seed_workspace(ws_plan, dist_id_by_slug, dry_run=args.dry_run)
        if ws is None or args.dry_run:
            ws_summary.append({"ws": None, "plan": ws_plan})
            continue
        staff_list = _seed_staff(ws.id, ws_plan["staff"], dry_run=args.dry_run)
        total_ws += 1
        total_staff += len(staff_list)
        n = _seed_instructions(ws.id, staff_list, ws_plan, dry_run=args.dry_run)
        total_instr += n
        ws_summary.append({"ws": ws, "plan": ws_plan, "staff_count": len(staff_list), "instr_count": n})

    print()
    print("=" * 70)
    print("ALL SEED DATA CREATED" if not args.dry_run else "DRY RUN SUMMARY")
    print("=" * 70)
    print(f"  distributors: {len(dist_id_by_slug)}   workspaces: {total_ws}   "
          f"staff: {total_staff}   demo instructions: {total_instr}")
    print()
    print("== Super admin ==")
    print("  /super")
    print("  PW: (settings.super_admin_password — .env)")
    print()
    print("== Distributor portal ==")
    print("  /distributor")
    for plan in DISTRIBUTORS:
        if plan["slug"] in dist_id_by_slug:
            print(f"  {plan['name']:25s}  email: {plan['owner_email']:30s}  PW: {plan['owner_password']}")
    print()
    print("== Customer workspaces ==")
    for item in ws_summary:
        ws_plan = item["plan"]
        ws = item.get("ws")
        if ws is None and not args.dry_run:
            continue
        url = f"/{ws_plan['dist_slug']}/{ws_plan['slug']}"
        plan_label = ws_plan["plan"].upper()
        print(f"  {ws_plan['name']:30s}  {url:25s}  plan={plan_label:11s}  PW: {ws_plan['owner_password']}")
        if not args.dry_run:
            for (_sid, login, label) in [(s, l, lb) for (s, l, lb) in [(x[0], x[1], x[2])
                                                                        for x in [(_id, _login, _disp) for (_id, _login, _disp) in []]]]:
                pass  # placeholder — staff are listed below
            # 스탭 로그인 정보
            for (login_id, display_name, password) in ws_plan["staff"]:
                print(f"    └ staff: {login_id:10s} ({display_name:18s})  PW: {password}")
    print()
    print("== Demo notes ==")
    print(f"  - PoPo の wholesale_enterprise を ¥4,500 に設定済み (볼륨 디스카운트 데모용)")
    print(f"  - 各ワークスペースに 1~4 件のデモ指示 + 応答が入っている")
    print(f"  - 응답 중 「NG」 가 일부 있음 → 「教材化候補」 데모로 활용 가능")
    print(f"  - Lemon Squeezy 연동은 별도 (이 시드는 결제 미연결 상태)")
    print()
    print("== Cleanup / re-run ==")
    print("  python scripts/seed_demo.py --reset    # 깨끗하게 다시")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
