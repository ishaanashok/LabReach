"""Build the one-page resume PDF attached to initial emails.

Content comes only from the student's resume (Ishaan_CV_Sept26) with the edits
the family approved (GPA 3.9, UC Scout removed, Innoflexion condensed, ReStep
India wording). The phone number is read from the gitignored
data/private/contact.yaml so it is never committed.

Usage: python tools/build_resume.py [output.pdf]
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Table, TableStyle

ROOT = Path(__file__).resolve().parent.parent
ACCENT = colors.HexColor("#1b5e63")
GITHUB = "https://github.com/ishaanashok"
LUNA_REPO = "https://github.com/ishaanashok/LUNA-TurbiditySensor"

FONT, BOLD, ITAL = "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"
SIZE = 9.4
LEAD = 11.3

body = ParagraphStyle("body", fontName=FONT, fontSize=SIZE, leading=LEAD)
bullet = ParagraphStyle("bullet", parent=body, leftIndent=9, bulletIndent=1, spaceAfter=0.8)
head = ParagraphStyle("head", fontName=BOLD, fontSize=9.4, leading=11, textColor=ACCENT, spaceBefore=5)
name = ParagraphStyle("name", fontName=FONT, fontSize=19, leading=22, alignment=TA_CENTER)
contact = ParagraphStyle("contact", parent=body, alignment=TA_CENTER, textColor=colors.HexColor("#444444"))
right = ParagraphStyle("right", parent=body, alignment=2)


def section(title: str) -> list:
    return [Paragraph(title.upper(), head), HRFlowable(width="100%", thickness=0.6, color=ACCENT, spaceAfter=1.5)]


def row(left: str, date: str) -> Table:
    t = Table([[Paragraph(left, body), Paragraph(date, right)]], colWidths=[5.2 * inch, 2.03 * inch], hAlign="LEFT")
    t.setStyle(TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                           ("TOPPADDING", (0, 0), (-1, -1), 2.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                           ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return t


def b(text: str) -> Paragraph:
    return Paragraph(text, bullet, bulletText="•")


def load_phone() -> str | None:
    path = ROOT / "data" / "private" / "contact.yaml"
    if path.exists():
        return (yaml.safe_load(path.read_text()) or {}).get("phone")
    return None


def build(out: Path) -> None:
    doc = SimpleDocTemplate(str(out), pagesize=letter, leftMargin=0.55 * inch, rightMargin=0.55 * inch,
                            topMargin=0.45 * inch, bottomMargin=0.4 * inch,
                            title="Ishaan Ashok - Resume", author="Ishaan Ashok")
    link = lambda url, label: f'<link href="{url}" color="#1b5e63">{label}</link>'  # noqa: E731
    bits = [p for p in (load_phone(), "ishaan.ashok123@gmail.com", "Fremont, CA",
                        link(GITHUB, "github.com/ishaanashok")) if p]
    s: list = [Paragraph("Ishaan Ashok", name), Paragraph(" | ".join(bits), contact)]

    s += section("Education")
    s += [row("<b>American High School</b>, Fremont, CA — Unweighted GPA: 3.9", "Expected graduation: June 2028"),
          Paragraph("<b>Relevant coursework:</b> AP Physics 1, AP Calculus BC, AP Computer Science A, "
                    "AP United States History, AP World History (5)", body),
          Paragraph("<b>Ohlone College</b> (GPA 4.0): Introduction to Programming with Python", body),
          Paragraph("<b>De Anza College</b> (GPA 4.0): CIS Programming Intermediate with C++", body)]

    s += section("Experience")
    s += [row("<b>Project ReStep</b> (501(c)(3) nonprofit) — <i>Founder / Design Lead</i>", "Aug 2025 – Present"),
          b("Founded and formally registered a 501(c)(3) nonprofit designing low-cost, adjustable prosthetics "
            "for underserved communities"),
          b("Engineered a modular, one-size-fits-all prosthetic with a screw-base attachment system, cutting per-unit "
            "cost from ~$1,750 for a traditional prosthetic to $30; manufactured with 3D-printed carbon fiber"),
          b("Distributing units at no cost to underserved communities in India, currently working with the "
            "state government there"),
          row("<b>Project LUNA</b> — <i>Co-Founder</i> (" + link(LUNA_REPO, "code &amp; CAD on GitHub") + ")",
              "Dec 2025 – Present"),
          b("Co-founded Luna and designed a low-cost turbidity sensor for stormwater monitoring, offering a ~$30K "
            "alternative to systems that can cost cities up to $6M"),
          b("Designed the full sensor system and piloted it in multiple Northern California cities, including "
            "Oakland, San Jose, and Concord; presented research findings at an IEEE conference"),
          row("<b>FIRST Tech Challenge Team “Ink and Metal”</b> — <i>Mechanical Lead / Captain</i>",
              "Nov 2024 – Present"),
          b("Designed the chassis, shooter, and intake systems in Onshape and led the team through building, "
            "iteration, and testing across a full competition season"),
          b("Directed the team to the State Championships, earning the Inspire Award (State Championship), "
            "Control Award, and Inspire Award (Qualifiers)"),
          b("Led events teaching special education students at our high school (3–4 classes per week, "
            "50+ students per class)"),
          row("<b>South Dakota School of Mines &amp; Technology</b> — <i>Internship, App Creation</i>",
              "Jun 2025 – Present"),
          b("Created a lab management app for researchers that uses AI to generate project reports; presented "
            "research at the BioWrap conference in Nebraska"),
          row("<b>Innoflexion</b> — <i>AI Engineer Intern</i>", "Apr 2026 – Aug 2026"),
          b("Designed and deployed autonomous data-extraction agents in Python (LangChain, LiteLLM) to structure "
            "information from unstructured internal documents")]

    s += section("Projects")
    s += [row("<b>AI Smart Glasses</b>", "Oct 2025 – Present"),
          b("Designed AI-powered smart glasses integrating an ESP32-CAM and a classifier model to analyze live "
            "video input and generate desired outputs"),
          row("<b>FIRST Tech Challenge: EPA Website</b>", "Jan 2025"),
          b("Built an open-source machine learning tool that recommends alliance partners to FTC teams, using "
            "FIRST Inspires API data; Python/C++ backend, TypeScript frontend, deployed and used live in competition")]

    s += section("Honors &amp; Activities")
    s += [b("<b>Eagle Scout</b> (Sep 2026); Senior Patrol Leader since Jan 2025, leading 100+ scouts; "
            "Crew Lead, Philmont backpacking trek"),
          b("<b>Youth Business Venture Competition</b> (Aug 2026): top 15 internationally of ~300 teams; "
            "presented Project LUNA to judges at Stanford University"),
          b("<b>FIRST Tech Challenge Inspire Award</b>, California state championships (Nov 2025); "
            "<b>1st Place and Best AI Project</b>, LG Hacks Hackathon (Apr 2024)"),
          b("President’s Volunteer Service Award (Dec 2023); Volunteer Teacher, Sewa ASPIRE (Summer 2024); "
            "Track and Field NCS Scholar Athlete (May 2025)")]

    s += section("Skills")
    s += [Paragraph("<b>Languages:</b> Python, C++, Java, JavaScript, TypeScript &nbsp;|&nbsp; "
                    "<b>Tools:</b> CAD &amp; 3D modeling (Onshape), 3D printing, mechanical prototyping, ESP32-CAM, "
                    "robotics systems, machine learning (classification &amp; regression), computer vision (OpenCV), "
                    "LangChain, LiteLLM, Flutter, AWS, Tkinter, Git", body)]

    doc.build(s)


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "private" / "Ishaan_Ashok_Resume.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    build(target)
    print(target)
