from datetime import date

from ronergate.cogs.girldle.parser import parse


def test_solved_5_of_8():
    content = (
        "Girldle 2026-05-13 5/8\n"
        "\U0001f7e5\U0001f7e9\U0001f7e5\U0001f7e5\U0001f7e5\n"
        "\U0001f7e5\U0001f7e9\U0001f7e5\U0001f7e5\U0001f7e5\n"
        "\U0001f7e5\U0001f7e9\U0001f7e5\U0001f7e5\U0001f7e5\n"
        "\U0001f7e5\U0001f7e9\U0001f7e9\U0001f7e9\U0001f7e5\n"
        "\U0001f7e9\U0001f7e9\U0001f7e9\U0001f7e9\U0001f7e9\n"
        "https://antistrategie.github.io/girl-kickers/unit-builder/#girldle"
    )
    result = parse(content)
    assert result is not None
    assert result.puzzle_date == date(2026, 5, 13)
    assert result.score == 5
    assert result.solved
    assert result.grid.count("\n") == 4


def test_fail_x_of_8():
    rows = "\n".join(["\U0001f7e5\U0001f7e5\U0001f7e5\U0001f7e5\U0001f7e5"] * 8)
    content = f"Girldle 2026-05-15 X/8\n{rows}\nhttps://example"
    result = parse(content)
    assert result is not None
    assert result.score is None
    assert not result.solved
    assert result.puzzle_date == date(2026, 5, 15)


def test_short_solve():
    content = (
        "Girldle 2026-05-15 3/8\n"
        "\U0001f7e5\U0001f7e5\U0001f7e5\U0001f7e5\U0001f7e9\n"
        "\U0001f7e5\U0001f7e5\U0001f7e5\U0001f7e9\U0001f7e9\n"
        "\U0001f7e9\U0001f7e9\U0001f7e9\U0001f7e9\U0001f7e9\n"
    )
    result = parse(content)
    assert result is not None
    assert result.score == 3


def test_leading_text():
    content = (
        "haha got it\n"
        "Girldle 2026-05-15 4/8\n"
        "\U0001f7e5\U0001f7e5\U0001f7e9\U0001f7e9\U0001f7e5\n"
        "\U0001f7e5\U0001f7e5\U0001f7e9\U0001f7e9\U0001f7e5\n"
        "\U0001f7e5\U0001f7e9\U0001f7e9\U0001f7e9\U0001f7e5\n"
        "\U0001f7e9\U0001f7e9\U0001f7e9\U0001f7e9\U0001f7e9\n"
    )
    result = parse(content)
    assert result is not None
    assert result.score == 4


def test_non_girldle_message():
    assert parse("hello world") is None
    assert parse("Girldle is cool") is None


def test_row_count_mismatch():
    # claims 5/8 but only 3 rows
    content = (
        "Girldle 2026-05-13 5/8\n"
        "\U0001f7e5\U0001f7e9\U0001f7e5\U0001f7e5\U0001f7e5\n"
        "\U0001f7e5\U0001f7e9\U0001f7e5\U0001f7e5\U0001f7e5\n"
        "\U0001f7e9\U0001f7e9\U0001f7e9\U0001f7e9\U0001f7e9\n"
    )
    assert parse(content) is None


def test_inconsistent_grid_width():
    content = (
        "Girldle 2026-05-13 2/8\n"
        "\U0001f7e5\U0001f7e9\U0001f7e5\n"
        "\U0001f7e9\U0001f7e9\U0001f7e9\U0001f7e9\U0001f7e9\n"
    )
    assert parse(content) is None


def test_bullet_separated_header():
    """New share format uses · between fields."""
    content = (
        "Girldle · 2026-05-18 · 1/8\n"
        "\U0001f7e9\U0001f7e9\U0001f7e9\U0001f7e9\U0001f7e9\n"
    )
    result = parse(content)
    assert result is not None
    assert result.score == 1
    assert result.puzzle_date == date(2026, 5, 18)


def test_watermark_marks_verified():
    """ZWSP count matching score sets verified=True."""
    content = (
        "Girldle · 2026-05-18 · 1/8​\n"
        "\U0001f7e9\U0001f7e9\U0001f7e9\U0001f7e9\U0001f7e9\n"
    )
    result = parse(content)
    assert result is not None
    assert result.verified is True


def test_no_watermark_marks_unverified():
    """No ZWSPs at all → unverified (legacy or forged)."""
    content = (
        "Girldle · 2026-05-18 · 1/8\n"
        "\U0001f7e9\U0001f7e9\U0001f7e9\U0001f7e9\U0001f7e9\n"
    )
    result = parse(content)
    assert result is not None
    assert result.verified is False


def test_wrong_watermark_count_marks_unverified():
    """ZWSP count not matching score → unverified."""
    content = (
        "Girldle · 2026-05-18 · 5/8​​\n"  # 2 ZWSPs but score is 5
        "\U0001f7e5\U0001f7e9\U0001f7e5\U0001f7e5\U0001f7e5\n"
        "\U0001f7e5\U0001f7e9\U0001f7e5\U0001f7e5\U0001f7e5\n"
        "\U0001f7e5\U0001f7e9\U0001f7e5\U0001f7e5\U0001f7e5\n"
        "\U0001f7e5\U0001f7e9\U0001f7e9\U0001f7e9\U0001f7e5\n"
        "\U0001f7e9\U0001f7e9\U0001f7e9\U0001f7e9\U0001f7e9\n"
    )
    result = parse(content)
    assert result is not None
    assert result.verified is False


def test_failed_share_watermark_is_eight():
    """X/8 means 8 ZWSPs are expected."""
    rows = "\n".join(["\U0001f7e5\U0001f7e5\U0001f7e5\U0001f7e5\U0001f7e5"] * 8)
    content = f"Girldle · 2026-05-18 · X/8{'​' * 8}\n{rows}"
    result = parse(content)
    assert result is not None
    assert result.score is None
    assert result.verified is True


def test_yellow_squares_accepted():
    content = (
        "Girldle 2026-05-13 4/8\n"
        "\U0001f7e5\U0001f7e8\U0001f7e5\U0001f7e5\U0001f7e5\n"
        "\U0001f7e5\U0001f7e8\U0001f7e8\U0001f7e5\U0001f7e5\n"
        "\U0001f7e5\U0001f7e9\U0001f7e8\U0001f7e9\U0001f7e5\n"
        "\U0001f7e9\U0001f7e9\U0001f7e9\U0001f7e9\U0001f7e9\n"
    )
    result = parse(content)
    assert result is not None
    assert result.score == 4
    assert "\U0001f7e8" in result.grid  # yellow preserved in stored grid
