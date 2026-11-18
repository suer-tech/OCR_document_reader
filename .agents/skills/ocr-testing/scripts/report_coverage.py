"""Render visual reports from an offline pytest and coverage.py run."""

import json
from html import escape
from pathlib import Path
from string import Template

from PIL import Image, ImageDraw, ImageFont


def format_percent(covered, total):
    return f"{covered / total:.1%}".replace(".", ",") if total else "—"


def measured_files(directory, project):
    data = json.loads((directory / "coverage.json").read_text(encoding="utf-8"))
    status = directory / "html" / "status.json"
    html_files = json.loads(status.read_text(encoding="utf-8")).get("files", {}) if status.is_file() else {}
    pages = {Path(item["index"]["file"]): item["index"]["url"]
             for item in html_files.values() if item.get("index")}
    files = []
    for name, entry in data["files"].items():
        path = Path(name)
        summary = entry["summary"]
        files.append({
            "name": path.name,
            "path": path.relative_to(project).as_posix() if path.is_relative_to(project) else path.as_posix(),
            "lines": (summary["covered_lines"], summary["num_statements"]),
            "branches": (summary["covered_branches"], summary["num_branches"]),
            "missing_lines": entry["missing_lines"],
            "missing_branches": entry["missing_branches"],
            "page": f"html/{pages[path]}" if path in pages else "",
        })
    return files


def indicators(files):
    for key, label in (("lines", "Строки кода"), ("branches", "Ветви условий")):
        covered = sum(file[key][0] for file in files)
        total = sum(file[key][1] for file in files)
        yield label, format_percent(covered, total), f"{covered} из {total}" if total else "Нет данных"


def test_result(stats, exit_code):
    if exit_code == 0:
        return "Прогон успешен", "ok"
    if exit_code == 1 and (stats.get("failed") or stats.get("error")):
        return "Есть падения", "failed"
    return "Прогон не завершён", "failed"


def gaps(file, label, entries):
    if not entries:
        return ""
    items = []
    for line, target in entries[:20]:
        text = str(line) if target is None else f"{line} → {target if target > 0 else 'выход'}"
        if file["page"]:
            text = f'<a href="{escape(file["page"])}#t{line}">{text}</a>'
        items.append(f"<li>{text}</li>")
    remaining = f"<p>Ещё {len(entries) - 20} в полном отчёте по файлу.</p>" if len(entries) > 20 else ""
    return f'<div><p><strong>{label} · {len(entries)}</strong></p><ul class="gaps">{"".join(items)}</ul>{remaining}</div>'


def file_row(file, prefix):
    lines, statements = file["lines"]
    branches, choices = file["branches"]
    missing = statements - lines + choices - branches
    matches = str(file["name"].startswith(prefix)).lower()
    link = f'<a href="{escape(file["page"])}">Открыть код с подсветкой</a>' if file["page"] else ""
    missing_lines = [(line, None) for line in file["missing_lines"]]
    body = gaps(file, "Строки", missing_lines) + gaps(file, "Переходы", file["missing_branches"])
    if not body:
        body = "<p>Все измеренные строки и переходы выполнены.</p>"
    line_meter = f'<progress aria-label="Покрытие строк" value="{lines}" max="{statements}"></progress>' if statements else ""
    branch_meter = f'<progress aria-label="Покрытие ветвей" value="{branches}" max="{choices}"></progress>' if choices else ""
    return f'''<details data-match="{matches}" data-name="{escape(file["path"])}"
        data-missing="{missing}" data-branches="{branches / choices if choices else 1}">
        <summary class="file">
            <span><strong>{escape(file["name"])}</strong><small>{escape(file["path"])}</small></span>
            <span>{format_percent(lines, statements)}<small>{lines} из {statements}</small>{line_meter}</span>
            <span>{format_percent(branches, choices)}<small>{branches} из {choices}</small>{branch_meter}</span>
            <span>{missing}</span>
        </summary>
        <div class="detail">{body}{link}</div>
    </details>'''


def font(size, bold=False):
    names = ("segoeuib.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf") if bold else (
        "segoeui.ttf", "DejaVuSans.ttf", "Arial.ttf",
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_infographic(path, scope, stats, result, files):
    image = Image.new("RGB", (1280, 830), "#eef3fa")
    draw = ImageDraw.Draw(image)
    ink, muted, blue, teal = "#152844", "#435577", "#205ad1", "#087e75"

    def text(x, y, value, size, color=ink, bold=False):
        draw.text((x, y), value, fill=color, font=font(size, bold))

    text(54, 42, "Покрытие тестами", 54, bold=True)
    text(58, 117, scope, 26, muted)
    test_text = f"{stats.get('failed', 0) + stats.get('error', 0)} падений · {stats.get('skipped', 0)} пропущено"
    cards = [("Тесты", str(stats.get("passed", 0)), test_text), *indicators(files)]
    for index, (label, value, detail) in enumerate(cards):
        x = 54 + index * 397
        draw.rounded_rectangle((x, 174, x + 377, 335), radius=18, fill="white")
        text(x + 22, 189, label, 24, muted)
        text(x + 22, 223, value, 49, teal if index == 0 and result == 0 else blue, True)
        text(x + 22, 293, detail, 21, muted)
    status, state = test_result(stats, result)
    text(56, 357, status, 23, teal if state == "ok" else "#aa3e2b", True)
    text(56, 408, "Файлы с пропущенными переходами", 29, bold=True)
    ordered = sorted(files, key=lambda file: len(file["missing_branches"]), reverse=True)
    shown = [file for file in ordered if file["missing_branches"]][:5]
    if not shown:
        text(56, 470, "Пропущенных переходов нет" if files else "Нет данных покрытия", 24, muted)
    for index, file in enumerate(shown):
        y = 474 + index * 57
        covered, total = file["branches"]
        label = file["name"]
        if len(label) > 36:
            label = label[:33] + "…"
        text(56, y, label, 23)
        draw.rounded_rectangle((516, y + 8, 941, y + 24), radius=8, fill="#d7e0ef")
        width = round(425 * covered / total)
        if width:
            draw.rounded_rectangle((516, y + 8, 516 + width, y + 24), radius=8, fill=blue)
        text(965, y, f"{format_percent(covered, total)}  ·  {total - covered} проп.", 22, muted)
    text(56, 778, "Покрытие показывает, какие строки и переходы выполнили тесты.", 20, muted)
    image.save(path)


def write_reports(directory, project, stats, result, selectors, prefix, has_coverage):
    files = measured_files(directory, project) if has_coverage else []
    scoped = [file for file in files if file["name"].startswith(prefix)]
    scope = f"Файлы с префиксом {prefix}" if prefix else "Все измеренные файлы"
    label, state = test_result(stats, result)
    failures = stats.get("failed", 0) + stats.get("error", 0)
    cards = [
        ("Тесты прошли", str(stats.get("passed", 0)), f"Падений {failures} · пропущено {stats.get('skipped', 0)}"),
        *indicators(scoped),
    ]
    assets = Path(__file__).resolve().parent.parent / "assets"
    template = Template((assets / "coverage_dashboard.html").read_text(encoding="utf-8"))
    cards_html = "".join(
        f"<article><small>{name}</small><strong>{value}</strong><span>{detail}</span></article>"
        for name, value, detail in cards
    )
    selector_html = "".join(f"<li><code>{escape(selector)}</code></li>" for selector in selectors)
    reports = '<a href="html/index.html">Полный отчёт coverage.py</a>' if has_coverage else ""
    (directory / "dashboard.html").write_text(template.substitute(
        scope=escape(scope), prefix=escape(prefix), state=state, result=label, cards=cards_html,
        rows="".join(file_row(file, prefix) for file in files), selectors=selector_html,
        reports=reports, run_counts=(
            f"Ожидаемые падения {stats.get('xfailed', 0)} · неожиданные успехи {stats.get('xpassed', 0)}"
        ),
    ), encoding="utf-8")
    make_infographic(directory / "infographic.png", scope, stats, result, scoped)
