"""Что видно, когда bootstrap на ноде падает.

Установщик Xray качает архив с полосой прогресса curl — сотни строк из
одних цифр. Ошибка приходит последней строкой, а сообщение обрезалось по
символам с конца, и в него попадали ровно проценты со скоростями.
Настоящая причина — «unzip: command not found» — уходила за границу.

Человеку при этом сообщается, что нода не подключилась, и предлагается
разбираться по выводу, в котором нет ничего, кроме полосы прогресса.
"""

from __future__ import annotations

from app.services.provisioning import _meaningful_tail

PROGRESS = """  % Total    % Received % Xferd  Average Speed   Time    Time     Time  Current
 33 20.1M   33 6895k    0     0   986k      0  0:00:20  0:00:07  0:00:13  978k
 50 20.1M   50 10.1M    0     0   943k      0  0:00:21  0:00:11  0:00:10  920k
 99 20.1M   99 20.1M    0     0   896k      0  0:00:23  0:00:23 --:--:--  922k
100 20.1M  100 20.1M    0     0   896k      0  0:00:23  0:00:23 --:--:--  922k"""


def test_the_actual_error_survives_the_progress_meter():
    """Регрессия: именно эта строка терялась."""
    output = PROGRESS + "\nmain: line 93: unzip: command not found\n"
    assert "unzip: command not found" in _meaningful_tail(output)


def test_the_progress_meter_itself_is_dropped():
    output = PROGRESS + "\nmain: line 93: unzip: command not found\n"
    tail = _meaningful_tail(output)
    assert "0:00:23" not in tail
    assert "20.1M" not in tail


def test_several_meaningful_lines_are_kept():
    """Причина редко умещается в одну строку."""
    output = (
        f"{PROGRESS}\n"
        "docker: permission denied\n"
        "See 'docker run --help'.\n"
        "exit 126"
    )
    tail = _meaningful_tail(output)
    assert "permission denied" in tail
    assert "exit 126" in tail


def test_nothing_but_progress_still_says_something():
    """Пустое сообщение хуже неудобного: если после фильтра ничего не
    осталось, лучше показать хвост как есть."""
    assert _meaningful_tail(PROGRESS).strip()


def test_empty_output_does_not_crash():
    assert _meaningful_tail("") == ""


def test_a_line_of_only_digits_is_not_mistaken_for_an_error():
    """Полоса прогресса — это строки из цифр; они не объясняют ничего."""
    assert _meaningful_tail("123 456 789\nреальная ошибка") == "реальная ошибка"


def test_the_tail_is_bounded():
    """Целиком лог bootstrap в сообщение об ошибке не помещается."""
    output = "\n".join(f"строка ошибки {n}" for n in range(200))
    assert len(_meaningful_tail(output).splitlines()) == 12
