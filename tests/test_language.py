import pytest
from agent.language import detect_language

@pytest.mark.parametrize("query", [
    "¿Quién fue Madero?",
    "Hola, cómo estás",
    "Háblame de la Revolución",
    "que pasó con Carranza",  # no tilde, has marker
    "Cuál es la capital de Japón",  # no tilde, has marker
])
def test_detects_spanish(query):
    assert detect_language(query) == "es"


@pytest.mark.parametrize("query", [
    "Who was Madero?",
    "Tell me about the revolution",
    "What is the capital of Japan",
    "Hello there",
])
def test_detects_english(query):
    assert detect_language(query) == "en"


def test_empty_string_defaults_to_english():
    assert detect_language("") == "en"


def test_none_defaults_to_english():
    assert detect_language(None) == "en"
