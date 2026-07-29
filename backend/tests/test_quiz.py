import pytest

def test_quiz_submit_incorrect(client):
    response = client.post(
        "/api/quiz/submit",
        json={
            "theta": 0.0,
            "difficulty": 0.0,
            "selected_option": "B",
            "correct_answer": "A",
            "topic": "Computer Science",
            "subtopic": "Introduction",
            "question": "What is 1 + 1?",
            "misconception": "Adding error"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["correct"] is False
    assert data["new_theta"] < 0.0  # theta should decrease

def test_quiz_submit_correct(client):
    response = client.post(
        "/api/quiz/submit",
        json={
            "theta": 0.0,
            "difficulty": 0.0,
            "selected_option": "A",
            "correct_answer": "A",
            "topic": "Computer Science",
            "subtopic": "Introduction",
            "question": "What is 1 + 1?"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["correct"] is True
    assert data["new_theta"] > 0.0  # theta should increase
