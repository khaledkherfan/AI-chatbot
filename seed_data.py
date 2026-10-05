"""
Professional seed data for JUST University Assistant.

Populates Redis with:
- Structured topic JSON (data:<canonical_key>) for cache hits
- Alias mappings + embeddings (when OpenAI is configured)
- Optional demo rows in logs/query_events.jsonl for admin analytics charts

Usage:
  python seed_data.py              # full seed (Redis + demo analytics)
  python seed_data.py --redis-only # skip analytics file
  python seed_data.py --analytics-only  # only append demo query_events.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime, timedelta

from services.redis_service import RedisService, normalize_alias
from services.embeddings_service import EmbeddingsService

# ---------------------------------------------------------------------------
# Canonical topics → aliases (Arabic + English mix)
# ---------------------------------------------------------------------------
SEED_ALIASES: dict[str, list[str]] = {
    "registration": [
        "تسجيل",
        "تسجيل المواد",
        "كيف اسجل",
        "registration",
        "enroll",
        "course registration",
        "تسجيل مواد",
        "how to register",
        "طريقة التسجيل",
        "فترة التسجيل",
        "إضافة وحذف المواد",
        "drop add",
    ],
    "fees": [
        "رسوم",
        "مصاريف",
        "fees",
        "tuition",
        "payment",
        "كم الرسوم",
        "تكلفة الساعة",
        "credit hour fee",
        "رسوم الجامعة",
        "مصاريف الدراسة",
        "how much tuition",
    ],
    "admissions": [
        "قبول",
        "قبولات",
        "admission",
        "apply",
        "تقديم",
        "طلب قبول",
        "شروط القبول",
        "admission requirements",
        "معدل القبول",
        "how to apply",
    ],
    "academic_calendar": [
        "تقويم",
        "تقويم اكاديمي",
        "academic calendar",
        "متى يبدأ الفصل",
        "semester start",
        "نهاية الفصل",
        "امتحانات نهائية",
        "عطلة",
        "holidays",
    ],
    "student_services": [
        "خدمات الطالب",
        "student services",
        "شؤون الطلاب",
        "مساعدة",
        "دعم الطلاب",
        "student affairs",
    ],
    "courses_schedule": [
        "جدول",
        "جدول المحاضرات",
        "schedule",
        "timetable",
        "مواعيد المحاضرات",
        "class schedule",
    ],
    "scholarships": [
        "منح",
        "منحة",
        "scholarship",
        "financial aid",
        "منح دراسية",
        "مساعدة مالية",
    ],
    "housing": [
        "سكن",
        "سكن طلابي",
        "housing",
        "dorm",
        "سكن جامعي",
        "accommodation",
    ],
    "library": [
        "مكتبة",
        "library",
        "استعارة كتب",
        "borrowing books",
        "مصادر",
    ],
    "graduation": [
        "تخرج",
        "graduation",
        "متطلبات التخرج",
        "graduation requirements",
        "شهادة",
        "diploma",
    ],
    "transcripts": [
        "كشف علامات",
        "transcript",
        "grades",
        "علامات",
        "سجل اكاديمي",
        "GPA",
    ],
    "engineering": [
        "هندسة",
        "كلية الهندسة",
        "engineering",
        "faculty of engineering",
    ],
    "it": [
        "تكنولوجيا المعلومات",
        "IT",
        "كلية تقنية المعلومات",
        "computer science",
        "برمجة",
        "software engineering",
    ],
    "contact": [
        "تواصل",
        "contact",
        "رقم الجامعة",
        "email",
        "كيف اتواصل",
    ],
}


def _doc_registration() -> dict:
    return {
        "topic": "registration",
        "title_ar": "تسجيل المواد الدراسية",
        "title_en": "Course registration",
        "summary_ar": "إرشادات عامة حول فترات التسجيل، إضافة وحذف المواد، والالتزام بالخطة الدراسية الموصى بها.",
        "summary_en": "General guidance on registration periods, add/drop, and following your recommended study plan.",
        "key_facts": [
            {"label_ar": "البوابة", "label_en": "Portal", "value": "عادة عبر النظام الأكاديمي للجامعة"},
            {"label_ar": "إضافة/حذف", "label_en": "Add/Drop", "value": "ضمن المهل الرسمية لكل فصل"},
        ],
        "official_hint": "راجع التقويم الأكاديمي وإعلانات عمادة القبول والتسجيل.",
    }


def _doc_fees() -> dict:
    return {
        "topic": "fees",
        "title_ar": "الرسوم والمصاريف",
        "title_en": "Tuition and fees",
        "summary_ar": "معلومات إطارية عن الرسوم؛ للأرقام الرسمية يُفضّل دائماً الاطلاع على المستندات المنشورة على موقع الجامعة.",
        "summary_en": "Framework information; always verify official PDFs on the university site.",
        "key_facts": [
            {
                "label_ar": "مستند مرجعي",
                "label_en": "Reference",
                "value": "services.just.edu.jo — مستندات الرسوم للبرامج النظامية",
            },
        ],
        "resources": [
            {"title": "Fees (regular) PDF", "url": "https://services.just.edu.jo/applic/Documents/Fees_Regular_2023.pdf"},
        ],
    }


def _doc_admissions() -> dict:
    return {
        "topic": "admissions",
        "title_ar": "القبول والتقديم",
        "title_en": "Admissions",
        "summary_ar": "شروط القبول تختلف حسب البرنامج والسنة؛ راجع الموقع الرسمي لآخر المعلومات.",
        "summary_en": "Requirements vary by program; check the official site for updates.",
        "key_facts": [
            {"label_ar": "المعدل", "label_en": "GPA", "value": "حسب تعليمات القبول لكل تخصص"},
        ],
    }


def _doc_calendar() -> dict:
    return {
        "topic": "academic_calendar",
        "title_ar": "التقويم الأكاديمي",
        "title_en": "Academic calendar",
        "summary_ar": "تواريخ الفصول والامتحانات تُعلن رسمياً كل عام.",
        "summary_en": "Semester and exam dates are published officially each year.",
        "key_facts": [
            {"label_ar": "تحديث", "label_en": "Updates", "value": "تابع إعلانات الجامعة"},
        ],
    }


def _doc_student_services() -> dict:
    return {
        "topic": "student_services",
        "title_ar": "خدمات الطلاب",
        "title_en": "Student services",
        "summary_ar": "نقاط التواصل مع عمادة شؤون الطلاب والخدمات المساندة.",
        "summary_en": "Entry points for student affairs and support services.",
        "key_facts": [],
    }


def _doc_schedule() -> dict:
    return {
        "topic": "courses_schedule",
        "title_ar": "الجدول الدراسي",
        "title_en": "Class schedule",
        "summary_ar": "يتم بناء الجدول وفق الخطة والشعب المتاحة؛ راجع النظام بعد اعتماد التسجيل.",
        "summary_en": "Built from your plan and offered sections; verify in the system after registration.",
        "key_facts": [],
    }


def _doc_scholarships() -> dict:
    return {
        "topic": "scholarships",
        "title_ar": "المنح والمساعدات",
        "title_en": "Scholarships",
        "summary_ar": "تتوفر منح ومساعدات وفق سياسات الجامعة؛ التفاصيل في صفحة عمادة شؤون الطلاب.",
        "summary_en": "Scholarships per university policy; see the deanship page.",
        "resources": [
            {
                "title": "Scholarships",
                "url": "https://www.just.edu.jo/ar/Deanships/DeanshipofStudentsAffairs/Pages/Scholarships.aspx",
            }
        ],
    }


def _doc_housing() -> dict:
    return {
        "topic": "housing",
        "title_ar": "السكن الطلابي",
        "title_en": "Student housing",
        "summary_ar": "معلومات عامة عن السكن الجامعي؛ للتسجيل والشروط اتبع الإعلانات الرسمية.",
        "summary_en": "General info; follow official announcements for application rules.",
        "key_facts": [],
    }


def _doc_library() -> dict:
    return {
        "topic": "library",
        "title_ar": "المكتبة",
        "title_en": "Library",
        "summary_ar": "خدمات الاستعارة والمصادر الرقمية وفق سياسة المكتبة المركزية.",
        "summary_en": "Borrowing and digital resources per central library policy.",
        "key_facts": [],
    }


def _doc_graduation() -> dict:
    return {
        "topic": "graduation",
        "title_ar": "التخرج",
        "title_en": "Graduation",
        "summary_ar": "استكمال الساعات والمتطلبات وفق الخطة الدراسية المعتمدة.",
        "summary_en": "Complete credits and requirements per the approved study plan.",
        "key_facts": [],
    }


def _doc_transcripts() -> dict:
    return {
        "topic": "transcripts",
        "title_ar": "الوثائق الأكاديمية",
        "title_en": "Transcripts and records",
        "summary_ar": "كشوف العلامات والوثائق الرسمية تُصدر عبر القنوات المعتمدة.",
        "summary_en": "Transcripts via official channels only.",
        "key_facts": [],
    }


def _doc_engineering() -> dict:
    return {
        "topic": "engineering",
        "title_ar": "كلية الهندسة",
        "title_en": "Faculty of Engineering",
        "summary_ar": "معلومات إطارية؛ للبرامج والأقسام راجع موقع الكلية.",
        "summary_en": "Overview; see faculty site for departments and programs.",
        "resources": [
            {
                "title": "Faculties",
                "url": "https://www.just.edu.jo/FacultiesandDepartments/Pages/default.aspx",
            }
        ],
    }


def _doc_it() -> dict:
    return {
        "topic": "it",
        "title_ar": "كلية تكنولوجيا المعلومات",
        "title_en": "IT Faculty",
        "summary_ar": "برامج مثل هندسة البرمجيات وعلوم الحاسوب والأمن السيبراني؛ الروابط الرسمية للخطط الدراسية.",
        "summary_en": "Programs such as SE, CS, Cybersecurity; use official study-plan PDFs.",
        "resources": [
            {
                "title": "Software Engineering plan",
                "url": "https://www.just.edu.jo/FacultiesandDepartments/it/Departments/SE/SiteAssets/Pages/Programs/SE_English_StudyPlan2025_12OCT2025.pdf",
            },
            {
                "title": "Computer Science plan",
                "url": "https://www.just.edu.jo/FacultiesandDepartments/it/Departments/cs/SiteAssets/Pages/Programs/CS%20Study%20Plan%202021%20-%20English%205-10-2021.pdf",
            },
            {
                "title": "Artificial Intelligence plan",
                "url": "https://www.just.edu.jo/FacultiesandDepartments/it/Departments/cs/SiteAssets/Pages/Programs/AI-English-StudyPlan-Final2024.pdf",
            },
            {
                "title": "Cybersecurity plan",
                "url": "https://www.just.edu.jo/FacultiesandDepartments/it/Departments/CyberSec/PublishingImages/Pages/Programs/Study%20Plan%20_%20Cybersecurity%20-English%20_FINAL_Oct2025.pdf",
            },
            {
                "title": "Robotics plan",
                "url": "https://www.just.edu.jo/FacultiesandDepartments/it/Departments/SE/Documents/Robo_Study%20Plan_In_English.pdf",
            },
            {
                "title": "Computer Engineering plan",
                "url": "https://www.just.edu.jo/FacultiesandDepartments/it/Departments/cpe/SiteAssets/Pages/Programs/CPE_English_2021-V19.pdf",
            },
        ],
    }


def _doc_contact() -> dict:
    return {
        "topic": "contact",
        "title_ar": "التواصل",
        "title_en": "Contact",
        "summary_ar": "للاستفسارات الرسمية استخدم القنوات المعتمدة المعلنة على موقع الجامعة.",
        "summary_en": "Use official channels listed on the university website.",
        "key_facts": [
            {"label_ar": "الموقع", "label_en": "Website", "value": "www.just.edu.jo"},
        ],
    }


SEED_DOCUMENTS: dict[str, dict] = {
    "registration": _doc_registration(),
    "fees": _doc_fees(),
    "admissions": _doc_admissions(),
    "academic_calendar": _doc_calendar(),
    "student_services": _doc_student_services(),
    "courses_schedule": _doc_schedule(),
    "scholarships": _doc_scholarships(),
    "housing": _doc_housing(),
    "library": _doc_library(),
    "graduation": _doc_graduation(),
    "transcripts": _doc_transcripts(),
    "engineering": _doc_engineering(),
    "it": _doc_it(),
    "contact": _doc_contact(),
}


def seed_redis_payloads() -> bool:
    """Store data:* and alias/emb mappings via RedisService.save_to_redis."""
    redis = RedisService()
    embeddings = EmbeddingsService()

    if not redis.is_connected():
        print("Redis not connected. Start Redis then run again.")
        return False

    emb_ok = embeddings.is_configured()
    if not emb_ok:
        print("OpenAI/embeddings not configured — seeding aliases and JSON without vectors.")

    print("\nSeeding topic documents and alias mappings...")
    print("=" * 60)

    total_emb = 0
    for canonical_key, aliases in SEED_ALIASES.items():
        doc = SEED_DOCUMENTS.get(canonical_key)
        if not doc:
            print(f"  SKIP (no document): {canonical_key}")
            continue

        alias_embeddings = {}
        if emb_ok and aliases:
            try:
                raw_embeddings = embeddings.generate_embeddings_batch(aliases)
                alias_embeddings = {
                    normalize_alias(alias): vec for alias, vec in raw_embeddings.items()
                }
                total_emb += len(alias_embeddings)
            except Exception as e:
                print(f"  WARN embeddings {canonical_key}: {e}")

        ok = redis.save_to_redis(
            canonical_key,
            doc,
            aliases=aliases,
            alias_embeddings=alias_embeddings if alias_embeddings else None,
        )
        status = "OK" if ok else "FAIL"
        print(f"  [{status}] {canonical_key} — {len(aliases)} aliases, emb={len(alias_embeddings)}")

    print("=" * 60)
    stats = redis.get_stats()
    print(f"Redis: data_keys={stats.get('total_data_keys')} aliases={stats.get('total_aliases')} emb={stats.get('total_embeddings')}")
    print(f"Total embedding vectors written this run: {total_emb}")
    return True


def seed_demo_query_events(count: int = 140) -> None:
    """Append realistic demo lines to logs/query_events.jsonl (spread over ~14 days)."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base_dir, "logs", "query_events.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)

    samples_ar = [
        ("كيف أسجل المواد في الفصل الحالي؟", "registration"),
        ("ما هي رسوم الساعة المعتمدة للبرنامج النظامي؟", "fees"),
        ("متى يبدأ الفصل الدراسي الثاني؟", "academic_calendar"),
        ("شروط القبول لكلية الهندسة؟", "admissions"),
        ("أين أجد خطة هندسة البرمجيات؟", "it"),
        ("هل يوجد منح للطلاب؟", "scholarships"),
        ("كيف أحصل على كشف علامات؟", "transcripts"),
        ("مواعيد امتحانات نهاية الفصل؟", "academic_calendar"),
        ("خدمات شؤون الطلاب", "student_services"),
        ("جدول المحاضرات لا يظهر", "courses_schedule"),
    ]
    samples_en = [
        ("How do I register for courses?", "registration"),
        ("What are the tuition fees?", "fees"),
        ("Scholarship application deadline", "scholarships"),
        ("Library borrowing hours", "library"),
        ("Contact the university", "contact"),
    ]

    lines = []
    now = datetime.utcnow()
    random.seed(42)
    for i in range(count):
        if random.random() < 0.65:
            q, topic = random.choice(samples_ar)
        else:
            q, topic = random.choice(samples_en)
        # slight variation
        if random.random() < 0.15:
            q = q + "؟" if not q.endswith("?") and not q.endswith("؟") else q

        days_ago = random.randint(0, 13)
        hours = random.randint(0, 23)
        mins = random.randint(0, 59)
        ts = now - timedelta(days=days_ago, hours=hours, minutes=mins)
        ts_iso = ts.replace(microsecond=0).isoformat() + "Z"

        # Prefer redis for repeated topics (simulates cache)
        source = random.choices(["redis", "live_web"], weights=[0.45, 0.55])[0]
        mode = random.choices(["stream", "sync"], weights=[0.85, 0.15])[0]

        lines.append(
            json.dumps(
                {
                    "ts": ts_iso,
                    "q": q,
                    "source": source,
                    "topic": topic,
                    "mode": mode,
                },
                ensure_ascii=False,
            )
            + "\n"
        )

    with open(path, "a", encoding="utf-8") as f:
        f.writelines(lines)

    print(f"Appended {count} demo rows to {path}")


def verify_seed():
    redis = RedisService()
    if not redis.is_connected():
        print("Cannot verify — Redis down.")
        return
    print("\nSpot checks (alias -> canonical):")
    checks = [
        ("fees", "fees"),
        ("registration", "registration"),
        ("scholarship", "scholarships"),
        ("schedule", "courses_schedule"),
    ]
    for alias, expect in checks:
        k = redis.resolve_alias(alias)
        ok = "OK" if k == expect else ("got:" + str(k))
        print(f"  {alias!r} -> {k!r} ({ok})")

    d = redis.fetch_from_redis("fees")
    if d:
        print(f"  data:fees topic={d.get('topic')!r} fields={list(d.keys())}")
    stats = redis.get_stats()
    print(f"\nRedis stats: {stats}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--redis-only", action="store_true")
    parser.add_argument("--analytics-only", action="store_true")
    args = parser.parse_args()

    print("JUST University Assistant — seed")
    print("=" * 60)

    if args.analytics_only:
        seed_demo_query_events()
        return 0

    if not seed_redis_payloads():
        return 1

    if not args.redis_only:
        seed_demo_query_events()

    verify_seed()
    return 0


if __name__ == "__main__":
    sys.exit(main())
