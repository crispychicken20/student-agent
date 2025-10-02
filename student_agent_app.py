
import streamlit as st
from datetime import time
import student_agent_core as core

st.set_page_config(page_title="Student Efficiency Agent", page_icon="⚙️", layout="wide")
st.title("⚙️ Student Efficiency Agent")
st.caption("Paste syllabus/email/task text → extract tasks → auto-plan → export to Calendar/CSV/Notion.")
st.header("Use to convert any tasks to a viable calendar to work on tasks.")

with st.sidebar:
    st.subheader("Planner Settings")
    daily_hours = st.slider("Max hours per day", 0.5, 12.0, core.DEFAULT_DAILY_HOURS, 0.5)
    start_h = st.number_input("Work start hour (24h)", 5, 12, core.WORK_START.hour)
    end_h = st.number_input("Work end hour (24h)", 13, 23, core.WORK_END.hour)
    # Update core config live
    core.WORK_START = time(int(start_h), 0)
    core.WORK_END = time(int(end_h), 0)
    st.write(f"Time zone: {core.APP_TZ}")

col1, col2 = st.columns(2)
with col1:
    st.subheader("1) Add inputs")
    raw_text = st.text_area("Paste emails/announcements/syllabi here", height=220,
                            placeholder="Example: Submit Lab 5 for CS61 on Fri Oct 3 by 11:59pm (~2h). Study Chapter 7 before quiz on 10/05.")
    uploads = st.file_uploader("Or upload text/markdown/PDF files", type=["txt","md","pdf"], accept_multiple_files=True)

with col2:
    st.subheader("2) Extract tasks")
    sample = st.checkbox("Use sample text")
    if sample and not raw_text:
        raw_text = """CS201: Submit Project 1 by Oct 6, 11:59pm (~3h). 
Calc 3: Review Lagrange multipliers before quiz 10/04 (~1.5h).
Email TA about office hours today.
Prepare resume bullets for ML intern application (due 10/08, ~2h)."""

    # Collect all texts
    input_blobs = []
    if raw_text.strip():
        input_blobs.append(("pasted", raw_text))
    if uploads:
        for f in uploads:
            b = f.read()
            txt = ""
            if f.type == "application/pdf" or f.name.lower().endswith(".pdf"):
                txt = core.read_pdf_bytes(b)
            else:
                try:
                    txt = b.decode("utf-8", errors="ignore")
                except Exception:
                    txt = ""
            if txt.strip():
                input_blobs.append((f.name, txt))

    extract_clicked = st.button("🔎 Extract")
    tasks = []
    if extract_clicked:
        if not input_blobs:
            st.warning("Please paste text or upload a file.")
        else:
            tasks = core.extract_tasks_from_inputs(input_blobs)
            if tasks:
                st.success(f"Found {len(tasks)} task(s).")
            else:
                st.info("No tasks found—try the sample text.")

    if "tasks_cache" not in st.session_state:
        st.session_state["tasks_cache"] = []
    if extract_clicked and tasks:
        st.session_state["tasks_cache"] = tasks

st.markdown("---")
tasks = st.session_state.get("tasks_cache", [])
if tasks:
    st.subheader("Tasks")
    st.dataframe(
        [{
            "P": t.priority,
            "Title": t.title,
            "Tag": t.tag,
            "Est (min)": t.est_minutes,
            "Due": t.due.astimezone(core.APP_TZ).strftime("%Y-%m-%d %H:%M") if t.due else "",
            "Source": t.source
        } for t in tasks],
        use_container_width=True
    )

    st.subheader("3) Plan & Export")
    if st.button("🗓️ Plan schedule"):
        blocks = core.plan_blocks(tasks, daily_hours=daily_hours)
        st.session_state["blocks_cache"] = blocks
        if blocks:
            st.success(f"Created {len(blocks)} calendar block(s).")
        else:
            st.info("No blocks created (perhaps tasks have no estimates, or all past?)")

    blocks = st.session_state.get("blocks_cache", [])

    # Preview if any blocks
    if blocks:
        st.write("Preview (first 10):")
        st.table([{
            "When": f"{b['start'].astimezone(core.APP_TZ).strftime('%a %b %d %I:%M %p')} → {b['end'].astimezone(core.APP_TZ).strftime('%I:%M %p')}",
            "What": b["title"]
        } for b in blocks[:10]])

    # --- Exports: always compute so buttons work ---
    ics_str = core.to_ics(blocks) if blocks else "BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//StudentAgent//EN\nEND:VCALENDAR\n"
    csv_str = core.tasks_to_csv(tasks) if tasks else "id,title,due,est_minutes,tag,priority,source\n"
    md_str = core.tasks_to_notion_md(tasks) if tasks else "# Tasks\n\n(No tasks extracted)\n"

    st.download_button(
        "⬇️ Download Calendar (.ics)",
        data=core.as_bytes(ics_str),
        file_name="study_plan.ics",
        mime="text/calendar",
    )

    st.download_button(
        "⬇️ Download Tasks (.csv)",
        data=core.as_bytes(csv_str),
        file_name="tasks.csv",
        mime="text/csv",
    )

    st.download_button(
        "⬇️ Copy Notion Markdown",
        data=core.as_bytes(md_str),
        file_name="tasks_notion.md",
        mime="text/markdown",
    )

    st.text_area("Notion Markdown (copy from here if you want):", md_str, height=220)
else:
    st.info("Add inputs and click **Extract** to get started.")
