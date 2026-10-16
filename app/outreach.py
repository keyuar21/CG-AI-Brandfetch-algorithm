"""Brand summaries and AI-drafted collaboration emails / LinkedIn messages from CouponzGuru."""
import re
import smtplib
from email.message import EmailMessage

import httpx

from . import config, llm, sheets
from .sources import UA

ABOUT_US = (
    f"{config.SENDER_COMPANY} ({config.SENDER_WEBSITE}) is a popular coupons, deals and cashback platform "
    "with a large audience of deal-seeking online shoppers across India and international markets. "
    "We partner with brands via affiliate programs, exclusive coupon codes, featured store pages, "
    "newsletters and social promotions to drive sales on a performance (CPA/CPS) basis."
)


def _site_blurb(website: str) -> str:
    if not website:
        return ""
    try:
        html = httpx.get(website, headers=UA, timeout=8, follow_redirects=True).text[:200_000]
    except Exception:
        return ""
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    desc = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)', html, re.I)
    return " ".join(x.group(1).strip() for x in (title, desc) if x)[:500]


def summary(brand: dict) -> str:
    blurb = _site_blurb(brand.get("website", ""))
    text, _ = llm.ask(
        "You write crisp brand briefs for a partnerships team. Plain text, no markdown.",
        f"Brand: {brand['brand_name']}\nCategory: {brand.get('catagory')}\nCountry: {brand.get('county')}\n"
        f"Website: {brand.get('website')}\nWebsite text: {blurb}\n\n"
        "Write 3 short sentences: what the brand sells, who it targets, and why it fits a coupon/deals "
        "partnership. If unsure about a fact, stay general rather than inventing it.",
    )
    text = text.strip()
    sheets.update_cell(brand["brand_name"], "summary", text)
    return text


def draft(brand: dict, kind: str) -> dict:
    ctx = (f"Brand: {brand['brand_name']}\nCategory: {brand.get('catagory')}\nCountry: {brand.get('county')}\n"
           f"Website: {brand.get('website')}\nAbout the brand: {brand.get('summary') or ''}\n\nAbout us: {ABOUT_US}\n"
           f"Sender: {config.SENDER_NAME}, {config.SENDER_COMPANY}")
    if kind == "email":
        text, _ = llm.ask(
            "You write warm, concise B2B partnership outreach emails. Respond with JSON only.",
            ctx + "\n\nWrite a collaboration email from CouponzGuru to this brand proposing an affiliate / "
            "coupon partnership. 120-170 words, personalised to the brand, 2-3 concrete benefits, a clear "
            "call to action asking them to reply with a time for a quick call. Greet the brand's team by name. "
            "Never invent statistics, user numbers, links or placeholders like [Name]; no emojis. "
            'Return {"subject": str, "body": str} — body is plain text with line breaks and a sign-off from the sender.',
        )
        data = llm.parse_json(text) or {}
        return {"subject": data.get("subject", f"Partnership: {brand['brand_name']} x CouponzGuru"),
                "body": _fill(data.get("body", text), brand)}
    text, _ = llm.ask(
        "You write short, friendly LinkedIn outreach messages. Plain text only.",
        ctx + "\n\nWrite a LinkedIn message (max 600 characters) from CouponzGuru to the brand's "
        "marketing/partnerships team proposing a coupon & affiliate collaboration. Personal, no hashtags, no emojis, "
        "no invented statistics, no placeholders like [Name].",
    )
    return {"body": _fill(text, brand)[:700]}


def _fill(text: str, brand: dict) -> str:
    """Replace placeholders small models sometimes leave despite instructions."""
    text = re.sub(r"\[(?:your|sender)[^\]]*\]", config.SENDER_NAME, text, flags=re.I)
    text = re.sub(r"\[[^\]]*name[^\]]*\]", f"{brand['brand_name']} team", text, flags=re.I)
    text = "\n".join(l for l in text.splitlines() if not re.search(r"\[[^\]]*\]|calendly\.com", l))
    return text.strip()


def send_email(to: str, subject: str, body: str):
    if not (config.SMTP_USER and config.SMTP_PASSWORD):
        raise RuntimeError("SMTP not configured")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = config.SMTP_USER, to, subject
    msg.set_content(body)
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as s:
        s.starttls()
        s.login(config.SMTP_USER, config.SMTP_PASSWORD)
        s.send_message(msg)
