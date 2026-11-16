import pytest
from app.models.attempt import QuestionAttempt
from app.models.classroom import ClassEnrollment, Classroom
from app.models.user import User
from app.database.connection import SessionLocal


def test_educator_dashboard_empty_without_students(client, teacher_auth_headers):
    """A tutor with no enrolled students gets an honest empty dashboard, not fake data."""
    response = client.get("/api/educators/dashboard", headers=teacher_auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["is_mock"] is False
    assert data["empty_reason"] == "no_students"
    assert data["students"] == []
    assert data["topic_perf"] == []
    assert data["misconceptions"] == []


def test_educator_dashboard_hides_other_students(client, teacher_auth_headers):
    """Students who are not in this tutor's classes never appear on the dashboard."""
    db = SessionLocal()
    outsider = User(name="Outside Student", email="outsider@example.com", hashed_password="x", role="student")
    db.add(outsider)
    db.commit()
    db.refresh(outsider)
    db.add(QuestionAttempt(user_id=outsider.id, topic="Maths", subtopic="Fractions", question_text="q",
                           selected_option="B", correct_option="A", is_correct=False,
                           misconception="Secret misconception", theta_before=0.0, theta_after=-0.2))
    db.commit()
    try:
        data = client.get("/api/educators/dashboard?topic=Maths", headers=teacher_auth_headers).json()
        assert data["students"] == []
        assert "Secret misconception" not in str(data)
    finally:
        db.query(QuestionAttempt).filter(QuestionAttempt.user_id == outsider.id).delete()
        db.delete(outsider)
        db.commit()
        db.close()


def test_educator_dashboard_real_data(client, teacher_auth_headers):
    db = SessionLocal()
    student = None
    classroom = None
    try:
        teacher = db.query(User).filter(User.email == "teacher_fixture@example.com").first()
        assert teacher is not None, "teacher_auth_headers fixture should have created this user"

        # A classroom (with an approved enrollment) is required for attempts to
        # be attributed to this teacher's dashboard.
        classroom = Classroom(name="Maths 101", subject="Maths", teacher_id=teacher.id)
        db.add(classroom)
        db.commit()
        db.refresh(classroom)

        student = User(
            name="Test Student",
            email="test_student@example.com",
            hashed_password="somepassword",
            role="student"
        )
        db.add(student)
        db.commit()
        db.refresh(student)

        db.add(ClassEnrollment(classroom_id=classroom.id, student_id=student.id, status="approved"))

        # Seed some attempts, attributed to the classroom above
        attempt1 = QuestionAttempt(
            user_id=student.id,
            classroom_id=classroom.id,
            topic="Maths",
            subtopic="Fractions",
            question_text="What is 1/2 + 1/4?",
            selected_option="A",
            correct_option="A",
            is_correct=True,
            misconception=None,
            theta_before=0.0,
            theta_after=0.3
        )
        attempt2 = QuestionAttempt(
            user_id=student.id,
            classroom_id=classroom.id,
            topic="Maths",
            subtopic="Fractions",
            question_text="What is 1/2 - 1/4?",
            selected_option="B",
            correct_option="A",
            is_correct=False,
            misconception="Subtracting unlike denominators",
            theta_before=0.3,
            theta_after=0.1
        )
        db.add(attempt1)
        db.add(attempt2)
        db.commit()

        response = client.get("/api/educators/dashboard?topic=Maths", headers=teacher_auth_headers)
        assert response.status_code == 200
        data = response.json()

        assert data["is_mock"] is False
        assert data["empty_reason"] is None
        assert data["kpis"]["active_students"] == "1/1"
        assert data["available_topics"] == [{"topic": "Maths", "attempts": 2}]
        assert data["topic_perf"] == [{"topic": "Fractions", "score": 50, "attempts": 2}]
        assert data["kpis"]["active_misconceptions"] == "1"
        assert len(data["students"]) == 1
        assert data["students"][0]["name"] == "Test Student"
        assert data["students"][0]["topics"] == 1  # 1 subtopic mastered (Fractions, because attempt1 is correct)
        assert data["students"][0]["accuracy"] == 50
        assert data["misconceptions"][0]["issue"] == "Subtracting unlike denominators"

        # Topic matching ignores case, and the all-topics view groups by topic
        assert client.get("/api/educators/dashboard?topic=maths", headers=teacher_auth_headers).json()["kpis"]["active_students"] == "1/1"
        all_topics = client.get("/api/educators/dashboard", headers=teacher_auth_headers).json()
        assert all_topics["group_label"] == "Topic"
        assert all_topics["topic_perf"][0]["topic"] == "Maths"
    finally:
        db.query(QuestionAttempt).filter(QuestionAttempt.classroom_id == (classroom.id if classroom else -1)).delete()
        if classroom is not None:
            db.query(ClassEnrollment).filter(ClassEnrollment.classroom_id == classroom.id).delete()
            db.delete(classroom)
        if student is not None:
            db.delete(student)
        db.commit()
        db.close()


def test_get_re_teaching_recommendations(client, teacher_auth_headers):
    response = client.get("/api/educators/re-teaching?topic=Maths", headers=teacher_auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "recommendation" in data
    assert len(data["recommendation"]) > 50


def test_re_teaching_requires_tutor(client):
    response = client.get("/api/educators/re-teaching?topic=Maths")
    assert response.status_code == 401
