from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import Optional
import httpx
from groq import Groq

from app.core.rate_limit import llm_rate_limit
from app.core.config import resolve_groq_api_key, config, groq_http_client
from app.database.session import get_db
from app.api.endpoints.users import get_current_teacher
from app.models.classroom import Classroom, ClassEnrollment
from app.models.user import User
from app.models.attempt import QuestionAttempt
from app.core.time_utils import utcnow

router = APIRouter()

def _theta_str(value: float) -> str:
    return f"+{value:.2f}" if value >= 0 else f"{value:.2f}"


def _theta_to_mastery(theta: Optional[float]) -> float:
    """Map IRT ability (theta, roughly -3..3) to a 0-100 mastery percentage."""
    if theta is None:
        return 0.0
    return max(0.0, min(100.0, ((theta + 3.0) / 6.0) * 100.0))


def _severity(pct: int) -> str:
    return "high" if pct >= 35 else "medium" if pct >= 20 else "low"


def _teacher_scope(teacher: User, db: Session):
    """Approved students in this teacher's classrooms, plus all of their attempts.

    Everything on the educator dashboard is limited to these students, so one
    tutor never sees another tutor's students or their answers.
    """
    class_ids = [row.id for row in db.query(Classroom.id).filter(Classroom.teacher_id == teacher.id).all()]
    if not class_ids:
        return [], []
    student_ids = [
        row.student_id
        for row in db.query(ClassEnrollment.student_id)
        .filter(ClassEnrollment.classroom_id.in_(class_ids), ClassEnrollment.status == "approved")
        .distinct()
        .all()
    ]
    if not student_ids:
        return [], []
    students = (
        db.query(User)
        .filter(User.id.in_(student_ids), User.role == "student")
        .order_by(User.name)
        .all()
    )
    attempts = (
        db.query(QuestionAttempt)
        .filter(QuestionAttempt.user_id.in_([s.id for s in students]))
        .all()
    )
    return students, attempts


def _matches_topic(attempt: QuestionAttempt, topic: str) -> bool:
    return (attempt.topic or "").strip().lower() == topic.strip().lower()


def _class_misconceptions(attempts: list) -> list:
    """Most common misconceptions among this class's wrong answers."""
    tagged = [
        a.misconception.strip()
        for a in attempts
        if a.is_correct is False and a.misconception and a.misconception.strip()
    ]
    if not tagged:
        return []
    counts: dict = {}
    for text in tagged:
        counts[text] = counts.get(text, 0) + 1
    items = []
    for text, count in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:8]:
        pct = int(round(count / len(tagged) * 100))
        items.append({"issue": text, "pct": pct, "count": count, "severity": _severity(pct)})
    return items


def _sort_key(attempt: QuestionAttempt):
    return attempt.timestamp or datetime.min


@router.get("/dashboard")
def get_educator_dashboard(
    topic: Optional[str] = Query(None, description="Topic to analyze; leave empty for all topics"),
    teacher: User = Depends(get_current_teacher),
    db: Session = Depends(get_db)
):
    """Class-level and student-level analytics for the tutor's own enrolled students.

    All numbers are computed from real attempts; when there is no data yet the
    response says so (``empty_reason``) instead of showing made-up figures.
    """
    topic = (topic or "").strip()
    students, all_attempts = _teacher_scope(teacher, db)

    # Topics the class has practised, most active first (for the topic picker)
    topic_counts: dict = {}
    for a in all_attempts:
        if a.topic:
            topic_counts[a.topic] = topic_counts.get(a.topic, 0) + 1
    available_topics = [
        {"topic": name, "attempts": count}
        for name, count in sorted(topic_counts.items(), key=lambda kv: kv[1], reverse=True)
    ]

    attempts = [a for a in all_attempts if _matches_topic(a, topic)] if topic else all_attempts

    student_records = []
    active_mastery = []
    for student in students:
        mine = sorted((a for a in attempts if a.user_id == student.id), key=_sort_key)
        if not mine:
            student_records.append({
                "id": student.id, "name": student.name, "email": student.email,
                "mastery": 0, "theta": "0.00", "theta_change": 0.0, "velocity": "No data",
                "trend": "stable", "topics": 0, "attempts": 0, "accuracy": 0, "last_active": None,
            })
            continue

        # Latest ability per topic -> mastery (averaged across topics in the all-topics view)
        latest_by_topic: dict = {}
        for a in mine:
            latest_by_topic[(a.topic or "").lower()] = a
        thetas = [a.theta_after for a in latest_by_topic.values() if a.theta_after is not None]
        theta_val = sum(thetas) / len(thetas) if thetas else 0.0
        mastery = sum(_theta_to_mastery(t) for t in thetas) / len(thetas) if thetas else 0.0
        active_mastery.append(mastery)

        deltas = [(a.theta_after or 0.0) - (a.theta_before or 0.0) for a in mine]
        avg_delta = sum(deltas) / len(deltas)
        velocity = "Fast" if avg_delta > 0.05 else "Slow" if avg_delta < -0.05 else "Steady"
        recent = mine[-5:]
        theta_change = (recent[-1].theta_after or 0.0) - (recent[0].theta_before or 0.0)
        trend = "up" if theta_change > 0.01 else "down" if theta_change < -0.01 else "stable"
        correct = sum(1 for a in mine if a.is_correct)

        student_records.append({
            "id": student.id,
            "name": student.name,
            "email": student.email,
            "mastery": int(round(mastery)),
            "theta": _theta_str(theta_val),
            "theta_change": round(theta_change, 2),
            "velocity": velocity,
            "trend": trend,
            "topics": len({a.subtopic for a in mine if a.is_correct and a.subtopic}),
            "attempts": len(mine),
            "accuracy": int(round(correct / len(mine) * 100)),
            "last_active": mine[-1].timestamp.isoformat() if mine[-1].timestamp else None,
        })

    active_students = [s for s in student_records if s["attempts"] > 0]
    avg_mastery = int(round(sum(active_mastery) / len(active_mastery))) if active_mastery else 0
    deltas = [(a.theta_after or 0.0) - (a.theta_before or 0.0) for a in attempts]
    avg_velocity = sum(deltas) / len(deltas) if deltas else 0.0
    misconceptions = _class_misconceptions(attempts)

    # Accuracy per subtopic (one topic) or per topic (all topics)
    groups: dict = {}
    for a in attempts:
        key = (a.subtopic or "General") if topic else (a.topic or "Other")
        groups.setdefault(key, []).append(a)
    topic_perf = sorted(
        (
            {
                "topic": name,
                "score": int(round(sum(1 for a in items if a.is_correct) / len(items) * 100)),
                "attempts": len(items),
            }
            for name, items in groups.items()
        ),
        key=lambda item: item["attempts"],
        reverse=True,
    )[:12]

    # Activity insights from real timestamps
    now = utcnow()
    day_ago, week_ago = now - timedelta(days=1), now - timedelta(days=7)
    active_today = {a.user_id for a in attempts if a.timestamp and a.timestamp >= day_ago}
    week = [a for a in attempts if a.timestamp and a.timestamp >= week_ago]
    improvers = sorted(
        (s for s in active_students if s["theta_change"] > 0),
        key=lambda s: s["theta_change"],
        reverse=True,
    )
    top = improvers[0] if improvers else None

    if not students:
        empty_reason = "no_students"
    elif not attempts:
        empty_reason = "no_attempts"
    else:
        empty_reason = None

    return {
        "is_mock": False,
        "empty_reason": empty_reason,
        "topic": topic or None,
        "available_topics": available_topics,
        "kpis": {
            "active_students": f"{len(active_students)}/{len(students)}",
            "avg_class_mastery": f"{avg_mastery}%",
            "active_misconceptions": str(len(misconceptions)),
            "avg_theta_velocity": _theta_str(avg_velocity),
        },
        "topic_perf": topic_perf,
        "group_label": "Subtopic" if topic else "Topic",
        "misconceptions": misconceptions,
        "students": student_records,
        "insights": {
            "top_improver": (
                {"id": top["id"], "name": top["name"], "email": top["email"], "theta_change": top["theta_change"]}
                if top else None
            ),
            "active_last_24h": len(active_today),
            "attempts_last_7d": len(week),
            "accuracy_last_7d": (
                int(round(sum(1 for a in week if a.is_correct) / len(week) * 100)) if week else None
            ),
        },
    }


def _group_by_subtopic(attempts: list) -> dict:
    groups: dict = {}
    for a in attempts:
        groups.setdefault(a.subtopic or "General", []).append(a)
    return groups


@router.get("/re-teaching", dependencies=[Depends(llm_rate_limit)])
def get_re_teaching_recommendations(
    topic: Optional[str] = Query(None, description="Topic to generate tips for; empty for all topics"),
    api_key: Optional[str] = Query(None, description="Optional per-request Groq API key override"),
    teacher: User = Depends(get_current_teacher),
    db: Session = Depends(get_db)
):
    """
    Queries Groq LLM to generate a customized re-teaching plan based on class misconceptions.
    """
    topic = (topic or "").strip()
    _, all_attempts = _teacher_scope(teacher, db)
    class_attempts = [a for a in all_attempts if _matches_topic(a, topic)] if topic else all_attempts
    if not topic:
        topic = "the topics this class has practised"

    api_key = resolve_groq_api_key(api_key)
    if not api_key:
        return {
            "recommendation": f"### AI Re-Teaching Recommendations for **{topic}**\n\n"
                              f"*(Note: `GROQ_API_KEY` is not configured in the backend `.env` file.)*\n\n"
                              f"**Standard Recommendations for {topic}:**\n"
                              f"1. **Break down complex subtopics**: Break instruction into step-by-step modular segments.\n"
                              f"2. **Use Scaffolded Hints**: Direct students to check foundations when errors occur.\n"
                              f"3. **Address Common Gaps**: Reinforce connections between theory and hands-on exercises."
        }
        
    misconceptions = _class_misconceptions(class_attempts)
    weak_areas = sorted(
        (
            (sub, sum(1 for a in items if a.is_correct) / len(items), len(items))
            for sub, items in _group_by_subtopic(class_attempts).items()
        ),
        key=lambda item: item[1],
    )[:4]

    if misconceptions:
        misconceptions_str = "\n".join(
            f"- {m['issue']} (Impacts {m['pct']}% of incorrect answers, Severity: {m['severity']})"
            for m in misconceptions
        )
    else:
        misconceptions_str = "- No tagged misconceptions recorded for this class yet; base the plan on the weakest areas below and on errors commonly seen in this topic."
    if weak_areas:
        misconceptions_str += "\n\nWeakest areas in this class (accuracy, number of answers):\n" + "\n".join(
            f"- {sub}: {round(acc * 100)}% correct over {n} answers" for sub, acc, n in weak_areas
        )
    
    prompt = f"""You are an expert tutor and educational psychologist.
The teacher requires an AI-driven re-teaching recommendation plan for their class.

Topic: {topic}
Detected Class Misconceptions:
{misconceptions_str}

Please generate a detailed, structured, and pedagogical re-teaching recommendation plan in Markdown.
You MUST cover:
1. **Misconception Deep-Dive**: Why are students making these specific errors? What are the root cognitive gaps?
2. **10-Minute Remedial Lesson Plan**: Provide a structured classroom warm-up or direct instruction block to address the issues.
3. **Analogy/Explanation Guide**: A memorable analogy or conceptual explanation to resolve the confusion.
4. **Targeted Check-for-Understanding Questions**: Write 2 multiple-choice check questions with explanation paths for incorrect choices.

Format the output clearly and professionally for a classroom teacher."""

    try:
        client = Groq(
            api_key=api_key,
            http_client=groq_http_client()
        )
        message = client.chat.completions.create(
            model=config.GROQ_MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        recommendation = message.choices[0].message.content.strip()
        return {"recommendation": recommendation}
    except Exception as e:
        return {
            "recommendation": f"### AI Re-Teaching Recommendations for **{topic}**\n\n"
                              f"Failed to generate custom recommendations due to API exception: {str(e)}"
        }
