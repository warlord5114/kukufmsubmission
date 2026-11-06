"""Nyaya Mitra — the chat interface.

Three things on screen that a general chatbot does not show, because they are
what makes this safe to use:

  * the case file, filling up as you talk, so you can see exactly what the
    system thinks is true about you and correct it
  * the deadline, computed and dated, because it is the one fact that decides
    whether anything else matters
  * the guard panel, showing anything the model tried to say that was blocked

Run:  streamlit run app.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from nyaya.casefile import DOMAIN_LABELS, fmt_money  # noqa: E402
from nyaya.kb import load_kb  # noqa: E402
from nyaya.lang import LANG_NAMES  # noqa: E402
from nyaya.llm.base import get_provider  # noqa: E402
from nyaya.session import Session  # noqa: E402

st.set_page_config(page_title="Nyaya Mitra", page_icon="⚖️", layout="wide")


@st.cache_resource(show_spinner=False)
def _kb():
    return load_kb()


def _new_session(model_spec: str) -> Session:
    try:
        provider = get_provider(model_spec)
    except Exception as exc:  # noqa: BLE001
        st.sidebar.error(f"{exc}")
        provider = get_provider("template")
    return Session(kb=_kb(), provider=provider)


# --- sidebar -----------------------------------------------------------------

st.sidebar.title("⚖️ Nyaya Mitra")
st.sidebar.caption("Legal help for three everyday problems in India.")

model_spec = st.sidebar.text_input(
    "Model", value=st.session_state.get("model_spec", "template"),
    help="template (no API key) · gemini:gemini-2.0-flash · groq:llama-3.3-70b-versatile",
)

if "sess" not in st.session_state or st.session_state.get("model_spec") != model_spec:
    st.session_state.model_spec = model_spec
    st.session_state.sess = _new_session(model_spec)
    st.session_state.messages = []

sess: Session = st.session_state.sess

if st.sidebar.button("Start over", use_container_width=True):
    st.session_state.sess = _new_session(model_spec)
    st.session_state.messages = []
    st.rerun()

st.sidebar.divider()

# --- case file ---------------------------------------------------------------

st.sidebar.subheader("Your case file")
c = sess.case
if c.domain:
    have, total = c.completeness()
    st.sidebar.progress(have / max(1, total), text=f"{DOMAIN_LABELS[c.domain]} · {have}/{total}")
    for slot in c.schema():
        v = c.facts.get(slot["key"])
        if v is None or str(v).strip() == "":
            continue
        if slot["type"] == "money":
            v = fmt_money(c.money(slot["key"]))
        elif slot["type"] == "bool":
            v = "Yes" if c.yes(slot["key"]) else "No"
        elif slot["type"] == "date":
            d_ = c.when(slot["key"])
            v = f"{d_:%d %b %Y}" if d_ else v
        label = slot["key"].replace("_", " ")
        st.sidebar.markdown(f"<small>**{label}:** {v}</small>", unsafe_allow_html=True)
    if c.skipped:
        st.sidebar.caption("Not answered: " + ", ".join(s.replace("_", " ") for s in c.skipped))
else:
    st.sidebar.caption("Nothing yet — tell it what happened.")

st.sidebar.caption(f"Language detected: **{LANG_NAMES.get(c.lang, c.lang)}**")

# --- deadline ----------------------------------------------------------------

a = sess.assessment
if a and a.deadline:
    d = a.deadline
    st.sidebar.divider()
    st.sidebar.subheader("Deadline")
    if d.expired:
        st.sidebar.error(f"Expired {d.expires_on:%d %b %Y}\n\n{abs(d.days_left)} days ago")
    elif d.urgent:
        st.sidebar.warning(f"{d.expires_on:%d %b %Y}\n\n{d.days_left} days left")
    else:
        st.sidebar.success(f"{d.expires_on:%d %b %Y}\n\n{d.days_left} days left")
    st.sidebar.caption(f"{d.label} from {d.runs_from_date:%d %b %Y}")

# --- guard -------------------------------------------------------------------

st.sidebar.divider()
st.sidebar.subheader("Hallucination guard")
findings = [f for r in sess.guard_reports for f in r.findings]
if not sess.guard_reports:
    st.sidebar.caption("Nothing checked yet.")
elif not findings:
    st.sidebar.success(
        f"Clean across {len(sess.guard_reports)} model reply(s). "
        "No invented law reached you."
    )
else:
    st.sidebar.error(f"{len(findings)} blocked claim(s)")
    for f in findings:
        with st.sidebar.expander(f"{f.kind} ({f.severity})"):
            st.write(f"**Caught:** `{f.snippet}`")
            st.write(f.message)
            st.caption(f"Action: {f.action}")
st.sidebar.caption(
    "The assistant may only cite a closed set of provisions. Anything else it "
    "writes — a section number, an Act, a court judgment — is stripped before "
    "you see it."
)

st.sidebar.divider()
st.sidebar.caption(f"Knowledge base: {_kb().summary()}")

# --- main --------------------------------------------------------------------

st.title("Nyaya Mitra")
st.caption(
    "A deposit a landlord will not return · salary that stopped · a refund that "
    "never came. In English, Hindi or Hinglish."
)

st.warning(
    "**This is not a lawyer and this is not legal advice.** It is general "
    "information about three specific problems, and it can be wrong. Free legal "
    "aid is a right for many people: call **15100** (NALSA), or go to the "
    "District Legal Services Authority at your district court.",
    icon="⚠️",
)

if not st.session_state.messages:
    with st.chat_message("assistant"):
        st.markdown(sess.greeting())
    cols = st.columns(3)
    examples = [
        ("Deposit", "My landlord is not returning my security deposit of 45000. "
                    "I moved out on 15 March 2026 and he keeps saying next week. "
                    "I have the agreement and the bank transfer."),
        ("Salary (Hinglish)", "Company ne 3 mahine se salary nahi di hai, kul 54000 "
                              "baaki hai. Meri salary 18000 mahina hai, August 2026 "
                              "se nahi mili."),
        ("Refund", "I ordered a washing machine for 24000 on 2 September 2026 and it "
                   "arrived with a cracked drum. They promised a refund on 10 "
                   "September 2026 and nothing has come."),
    ]
    for col, (label, text) in zip(cols, examples):
        if col.button(label, use_container_width=True):
            st.session_state.pending = text
            st.rerun()

for m in st.session_state.messages:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])

prompt = st.chat_input("Tell me what happened…") or st.session_state.pop("pending", None)

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Working it out…"):
            try:
                reply = sess.handle(prompt)
            except Exception as exc:  # noqa: BLE001
                reply = (
                    f"Something went wrong on my side: `{exc}`\n\n"
                    "That is a bug, not a judgement about your case. For anything "
                    "urgent, call **15100** for free legal aid."
                )
        st.markdown(reply)
    st.session_state.messages.append({"role": "assistant", "content": reply})
    st.rerun()

# --- quick actions -----------------------------------------------------------

if sess.stage in ("assessed", "practice") and st.session_state.messages:
    st.divider()
    a1, a2, a3 = st.columns(3)
    if a1.button("📄 Draft a legal notice", use_container_width=True):
        st.session_state.pending = "notice"
        st.rerun()
    if a2.button("🗣️ Practise the argument", use_container_width=True):
        st.session_state.pending = "practice"
        st.rerun()
    if a3.button("📋 My case file", use_container_width=True):
        st.session_state.pending = "summary"
        st.rerun()
