import streamlit as st
import bcrypt
import re
import io
import base64
import html
import pandas as pd
import anthropic
from pypdf import PdfReader
from supabase import create_client, Client

# -----------------------------------------------------------------------------
# გვერდის კონფიგურაცია (უნდა იყოს პირველი Streamlit ბრძანება)
# -----------------------------------------------------------------------------
st.set_page_config(
    page_title="EduAudit AI",
    page_icon="🛡️",
    layout="centered"
)

# -----------------------------------------------------------------------------
# ANTHROPIC CLAUDE API კონფიგურაცია
# გასაღები იკითხება .streamlit/secrets.toml-დან (არასოდეს ჩაწეროთ პირდაპირ კოდში!)
# -----------------------------------------------------------------------------
API_KEY = st.secrets.get("ANTHROPIC_API_KEY", "").strip()
if not API_KEY:
    st.error("ANTHROPIC_API_KEY ვერ მოიძებნა. დაამატეთ secrets.toml ფაილში.")
    st.stop()

client = anthropic.Anthropic(
    api_key=API_KEY,
    default_headers={"anthropic-version": "2026-10-21"},
)

# -----------------------------------------------------------------------------
# SUPABASE კავშირის კონფიგურაცია
# -----------------------------------------------------------------------------
SUPABASE_URL = st.secrets.get("supabase", {}).get("url", "YOUR_SUPABASE_URL")
SUPABASE_KEY = st.secrets.get("supabase", {}).get("key", "YOUR_SUPABASE_KEY")


@st.cache_resource
def init_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


supabase = init_supabase()

# -----------------------------------------------------------------------------
# სტილები (მუქი თემა)
# -----------------------------------------------------------------------------
st.markdown("""
<style>
    .stApp {
        background-color: #131314;
        color: #e3e3e3;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }

    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}

    .gemini-card {
        background-color: #1e1f20;
        border: 1px solid #333537;
        border-radius: 16px;
        padding: 24px;
        margin-bottom: 20px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.4);
    }

    [data-testid='stFileUploader'] {
        background-color: #ffffff;
        border-radius: 12px;
        padding: 15px;
        border: 2px dashed #d1d5db;
    }
    [data-testid='stFileUploader'] section {
        background-color: #ffffff !important;
    }
    [data-testid='stFileUploader'] label, [data-testid='stFileUploader'] small, [data-testid='stFileUploader'] span {
        color: #374151 !important;
    }

    .chat-bubble-user {
        background-color: #2b2c2f;
        border: 1px solid #3c3f44;
        padding: 16px;
        border-radius: 14px;
        margin-bottom: 12px;
        color: #f3f4f6;
        word-break: break-word;
    }

    .chat-bubble-ai {
        background-color: #131314;
        border: 1px solid #282a2c;
        padding: 16px;
        border-radius: 14px;
        margin-bottom: 12px;
        color: #e3e3e3;
        word-break: break-word;
        white-space: pre-wrap;
    }

    .stTextInput input, .stTextArea textarea {
        background-color: #1e1f20 !important;
        color: #e3e3e3 !important;
        border-radius: 12px !important;
        border: 1px solid #444746 !important;
        padding: 12px 16px !important;
    }

    .stButton button {
        background: linear-gradient(135deg, #8ab4f8 0%, #669df6 100%);
        color: #131314;
        border: none;
        border-radius: 12px;
        font-weight: 700;
        padding: 0.6rem 1.4rem;
        box-shadow: 0 4px 12px rgba(138, 180, 248, 0.2);
        transition: all 0.2s ease;
        width: 100%;
    }
    .stButton button:hover {
        background: linear-gradient(135deg, #aecbfa 0%, #8ab4f8 100%);
        box-shadow: 0 6px 16px rgba(138, 180, 248, 0.4);
        transform: translateY(-1px);
    }
</style>
""", unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# SESSION STATE
# შენიშვნა: ავტომატური შესვლა URL-ის (?user=...) საშუალებით წაშლილია,
# რადგან ნებისმიერს შეეძლო სხვა მომხმარებლად (მათ შორის ადმინად) შესვლა.
# -----------------------------------------------------------------------------
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
if 'user' not in st.session_state:
    st.session_state.user = None
if 'role' not in st.session_state:
    st.session_state.role = None
if 'auth_mode' not in st.session_state:
    st.session_state.auth_mode = "შესვლა"


# -----------------------------------------------------------------------------
# დამხმარე ფუნქციები
# -----------------------------------------------------------------------------
def validate_email(email):
    return re.match(r"^[\w\.-]+@[\w\.-]+\.\w+$", email)


def validate_password(password):
    return len(password) >= 8 and any(c.isupper() for c in password)


def esc(text):
    """HTML-ის ესქეიპინგი, რომ ტექსტმა გვერდი არ დააზიანოს."""
    return html.escape(str(text))


def read_uploaded_file(uploaded_file):
    """ატვირთული ფაილიდან ტექსტის ამოღება (PDF ან ჩვეულებრივი ტექსტი)."""
    name = uploaded_file.name.lower()
    raw = uploaded_file.getvalue()

    if name.endswith(".pdf"):
        reader = PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            reader.decrypt("")
        pages = []
        for i, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                pages.append(f"--- Page {i} ---\n{text}")
        if not pages:
            return "No extractable text found in PDF (it may be a scanned image)."
        return "\n\n".join(pages)

    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    for enc in ("utf-8-sig", "cp1251", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


# -----------------------------------------------------------------------------
# ავტორიზაციის ეკრანი
# -----------------------------------------------------------------------------
if not st.session_state.logged_in:
    st.markdown("<h1 style='text-align: center; color: #ffffff; font-weight: 800; margin-top: 20px;'>🛡️ EduAudit AI</h1>", unsafe_allow_html=True)
    st.markdown("<p style='text-align: center; color: #9aa0a6; margin-bottom: 25px; font-size: 14px;'>Enterprise-Grade Secure Document & Compliance Auditor</p>", unsafe_allow_html=True)

    col1, col2, col3 = st.columns([0.1, 2.8, 0.1])
    with col2:
        st.markdown('<div class="gemini-card">', unsafe_allow_html=True)

        t1, t2 = st.columns(2)
        with t1:
            if st.button("🔑 შესვლა", use_container_width=True):
                st.session_state.auth_mode = "შესვლა"
                st.rerun()
        with t2:
            if st.button("📝 რეგისტრაცია", use_container_width=True):
                st.session_state.auth_mode = "რეგისტრაცია"
                st.rerun()

        st.markdown(f"<p style='text-align: center; color: #8ab4f8; font-size: 13px; font-weight: 600; margin-top: 15px;'>რეჟიმი: {st.session_state.auth_mode}</p>", unsafe_allow_html=True)

        email = st.text_input("ელფოსტა", placeholder="name@example.com")
        password = st.text_input("პაროლი", type="password", placeholder="მინ. 8 სიმბოლო, 1 დიდი ასო")

        if st.session_state.auth_mode == "რეგისტრაცია":
            if st.button("ანგარიშის შექმნა", use_container_width=True):
                if not validate_email(email):
                    st.error("არასწორი ელფოსტის ფორმატი.")
                elif not validate_password(password):
                    st.error("პაროლი უნდა იყოს მინიმუმ 8 სიმბოლო და შეიცავდეს 1 დიდ ასოს.")
                else:
                    try:
                        # ვამოწმებთ, უკვე ხომ არ არსებობს ეს ელფოსტა
                        existing_user = supabase.table("users").select("email").eq("email", email).execute()
                        if existing_user.data:
                            st.error("ეს ელფოსტა უკვე დაკავებულია.")
                        else:
                            hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

                            # ვითვლით რამდენი მომხმარებელია ბაზაში
                            all_users = supabase.table("users").select("id", count="exact").execute()
                            user_count = all_users.count if all_users.count is not None else len(all_users.data)

                            # პირველი მომხმარებელი ან მთავარი ელფოსტა ხდება ადმინი
                            role = "ADMIN" if (user_count == 0 or email == "mmanagadze123@gmail.com") else "USER"

                            supabase.table("users").insert({
                                "email": email,
                                "password_hash": hashed,
                                "role": role
                            }).execute()

                            st.success("რეგისტრაცია წარმატებულია! შეგიძლიათ შეხვიდეთ.")
                    except Exception as e:
                        st.error(f"შეცდომა რეგისტრაციისას: {e}")
        else:
            if st.button("შესვლა", use_container_width=True):
                res = supabase.table("users").select("password_hash, role").eq("email", email).execute()

                login_success = False
                user_role = None
                if res.data:
                    stored_hash = res.data[0]["password_hash"]
                    user_role = res.data[0]["role"]
                    if isinstance(stored_hash, str):
                        stored_hash = stored_hash.encode('utf-8')
                    if bcrypt.checkpw(password.encode('utf-8'), stored_hash):
                        login_success = True

                if login_success:
                    st.session_state.logged_in = True
                    st.session_state.user = email
                    st.session_state.role = user_role
                    st.rerun()
                else:
                    st.error("არასწორი ელფოსტა ან პაროლი.")
        st.markdown('</div>', unsafe_allow_html=True)

# -----------------------------------------------------------------------------
# მთავარი სამუშაო სივრცე
# -----------------------------------------------------------------------------
else:
    col_a, col_b = st.columns([3, 1])
    with col_a:
        st.markdown("### 🛡️ EduAudit Workspace")
        role_badge = "👑 ადმინისტრატორი" if st.session_state.role == "ADMIN" else "👤 მომხმარებელი"
        st.markdown(f"<span style='color: #9aa0a6; font-size: 13px;'>მომხმარებელი: <b>{esc(st.session_state.user)}</b> ({role_badge})</span>", unsafe_allow_html=True)
    with col_b:
        st.markdown("<div style='text-align: right;'>", unsafe_allow_html=True)
        if st.button("🚪 გასვლა"):
            st.session_state.logged_in = False
            st.session_state.user = None
            st.session_state.role = None
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    # ადმინს აქვს ნავიგაცია სამუშაო სივრცესა და პანელს შორის
    if st.session_state.role == "ADMIN":
        app_mode_selection = st.pills(
            "Navigation",
            ["📁 სამუშაო სივრცე", "📊 ადმინისტრატორის პანელი"],
            default="📁 სამუშაო სივრცე",
            label_visibility="collapsed"
        )
        app_mode = "admin" if app_mode_selection == "📊 ადმინისტრატორის პანელი" else "workspace"
    else:
        app_mode = "workspace"

    # -------------------------------------------------------------------------
    # სამუშაო სივრცე: ფაილის ატვირთვა და ანალიზი
    # -------------------------------------------------------------------------
    if app_mode == "workspace":
        st.markdown('<div class="gemini-card" style="margin-top: 15px;">', unsafe_allow_html=True)
        st.markdown("""
            <div style='display: flex; align-items: center; gap: 10px; margin-bottom: 15px;'>
                <span style='font-size: 24px;'>📁</span>
                <h3 style='margin: 0; color: #f3f4f6; font-size: 20px;'>ფაილის ატვირთვა და ანალიზი</h3>
            </div>
        """, unsafe_allow_html=True)

        uploaded_files = st.file_uploader(
            "📂 ატვირთეთ ნებისმიერი ფაილი (PNG, PDF, TXT და ა.შ.)",
            type=None,
            accept_multiple_files=True
        )

        if uploaded_files:
            names_text = ", ".join(esc(f.name) for f in uploaded_files)
            st.markdown(f"<p style='color: #1f2937; font-size: 13px; margin-top: 5px; background: #e5e7eb; padding: 6px; border-radius: 6px;'>არჩეულია ფაილი: <b>{names_text}</b></p>", unsafe_allow_html=True)

        user_prompt = st.text_area("შეტყობინება AI-სთვის", placeholder="შეიყვანეთ დავალება...", height=90)

        if st.button("✨ ანალიზის გაშვება"):
            try:
                content_blocks = []
                if user_prompt:
                    content_blocks.append({"type": "text", "text": user_prompt})

                if uploaded_files:
                    for uploaded_file in uploaded_files:
                        file_name = uploaded_file.name.lower()
                        file_bytes = uploaded_file.getvalue()

                        # სურათების დამუშავება
                        if file_name.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
                            media_type = "image/png"
                            if file_name.endswith((".jpg", ".jpeg")):
                                media_type = "image/jpeg"
                            elif file_name.endswith(".webp"):
                                media_type = "image/webp"
                            elif file_name.endswith(".gif"):
                                media_type = "image/gif"

                            b64_data = base64.b64encode(file_bytes).decode("utf-8")
                            content_blocks.append({
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": b64_data
                                }
                            })
                        else:
                            # PDF და ტექსტური ფაილები
                            try:
                                file_text = read_uploaded_file(uploaded_file)
                                content_blocks.append({"type": "text", "text": f"\n\nDocument Content ({uploaded_file.name}):\n{file_text}"})
                            except Exception as fe:
                                st.error(f"ფაილის წაკითხვის შეცდომა: {fe}")
                                st.stop()

                if not content_blocks:
                    content_blocks.append({"type": "text", "text": "გთხოვთ გააანალიზოთ ატვირთული ფაილი."})

                with st.spinner("Claude ამუშავებს მონაცემებს..."):
                    message = client.messages.create(
                        model="claude-opus-5-5",
                        max_tokens=2048,
                        messages=[{"role": "user", "content": content_blocks}]
                    )

                    answer = next(block.text for block in message.content if hasattr(block, 'text'))

                    # ვინახავთ ისტორიაში
                    supabase.table("history").insert({
                        "username": st.session_state.user,
                        "prompt": user_prompt if user_prompt else (f"File: {', '.join(f.name for f in uploaded_files)}" if uploaded_files else "File: None"),
                        "response": answer
                    }).execute()

                    st.markdown("### 📊 პასუხი:")
                    st.markdown(f"<div class='chat-bubble-ai'>{esc(answer)}</div>", unsafe_allow_html=True)

            except Exception as e:
                st.error(f"შეცდომა API-სთან დაკავშირებით: {e}")
        st.markdown('</div>', unsafe_allow_html=True)

        # ბოლო 5 ანალიზის ისტორია
        st.markdown("### 🕒 ძველი ანალიზების ისტორია")
        history_res = supabase.table("history").select("prompt, response, timestamp").eq("username", st.session_state.user).order("timestamp", desc=True).limit(5).execute()
        history = history_res.data

        if history:
            for h in history:
                st.markdown(f"<div class='chat-bubble-user'><b>შეკითხვა:</b> {esc(h['prompt'][:120])}...<br><small style='color: #9aa0a6;'>დრო: {esc(h['timestamp'])}</small></div>", unsafe_allow_html=True)
                st.markdown(f"<div class='chat-bubble-ai'><b>AI პასუხი:</b> {esc(h['response'][:400])}...</div>", unsafe_allow_html=True)
        else:
            st.info("ისტორია ჯერ ცარიელია.")

    # -------------------------------------------------------------------------
    # ადმინისტრატორის პანელი
    # -------------------------------------------------------------------------
    elif app_mode == "admin":
        st.markdown("### 📊 ადმინისტრატორის მართვის პანელი")

        users_count_res = supabase.table("users").select("id", count="exact").execute()
        total_users = users_count_res.count if users_count_res.count is not None else len(users_count_res.data)

        history_count_res = supabase.table("history").select("id", count="exact").execute()
        total_queries = history_count_res.count if history_count_res.count is not None else len(history_count_res.data)

        m1, m2 = st.columns(2)
        with m1:
            st.markdown(f"""
                <div class='gemini-card' style='text-align: center;'>
                    <h4 style='color: #9aa0a6; margin: 0;'>სულ მომხმარებელი</h4>
                    <h2 style='color: #8ab4f8; margin: 10px 0 0 0;'>{total_users}</h2>
                </div>
            """, unsafe_allow_html=True)
        with m2:
            st.markdown(f"""
                <div class='gemini-card' style='text-align: center;'>
                    <h4 style='color: #9aa0a6; margin: 0;'>სულ გაგზავნილი ანალიზი</h4>
                    <h2 style='color: #8ab4f8; margin: 10px 0 0 0;'>{total_queries}</h2>
                </div>
            """, unsafe_allow_html=True)

        # ადმინის უფლების მინიჭება
        st.markdown("#### 👑 მომხმარებლისთვის ადმინის უფლების მინიჭება")
        target_promote_email = st.text_input("მომხმარებლის ელფოსტა", placeholder="user@example.com", key="promote_email_input")
        if st.button("მივანიჭო ADMIN სტატუსი"):
            if target_promote_email:
                user_check = supabase.table("users").select("*").eq("email", target_promote_email).execute()
                if user_check.data:
                    supabase.table("users").update({"role": "ADMIN"}).eq("email", target_promote_email).execute()
                    st.success(f"მომხმარებელი ({target_promote_email}) წარმატებით გახდა ადმინისტრატორი!")
                else:
                    st.error("ასეთი ელფოსტით მომხმარებელი ბაზაში არ მოიძებნა.")
            else:
                st.warning("გთხოვთ ჩაწეროთ ელფოსტა.")

        # მომხმარებლების ცხრილი
        st.markdown("#### 👥 რეგისტრირებული მომხმარებლები")
        users_res = supabase.table("users").select("id, email, role, created_at").order("created_at", desc=True).execute()
        users_data = users_res.data
        if users_data:
            users_df = pd.DataFrame(users_data)
            users_df = users_df.rename(columns={"id": "ID", "email": "ელფოსტა", "role": "როლი", "created_at": "რეგისტრაციის დრო"})
            st.dataframe(users_df, use_container_width=True)
        else:
            st.info("მომხმარებლები არ მოიძებნა.")

        # სისტემის სრული ისტორია
        st.markdown("#### 📜 სისტემის სრული ისტორია (ყველა მომხმარებელი)")
        all_history_res = supabase.table("history").select("username, prompt, response, timestamp").order("timestamp", desc=True).limit(20).execute()
        all_history = all_history_res.data
        if all_history:
            for ah in all_history:
                st.markdown(f"<div class='chat-bubble-user'><b>მომხმარებელი:</b> {esc(ah['username'])}<br><b>შეკითხვა:</b> {esc(ah['prompt'][:150])}...<br><small style='color: #9aa0a6;'>დრო: {esc(ah['timestamp'])}</small></div>", unsafe_allow_html=True)
                st.markdown(f"<div class='chat-bubble-ai'><b>AI პასუხი:</b> {esc(ah['response'][:300])}...</div>", unsafe_allow_html=True)
        else:
            st.info("ისტორია ჯერ ცარიელია.")