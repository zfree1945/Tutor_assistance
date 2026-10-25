"""家教筛单器 v1.0.2：运行 `streamlit run app.py`。"""

import hashlib
import json

import streamlit as st

from tutor_screen import batch_store, map_ui
from tutor_screen.ui_theme import apply_theme, sidebar_brand
from tutor_screen.results_layout import desktop_results

from tutor_screen.config_store import ConfigStoreError, ModelConfig, SavedSettings, load_settings, save_config
from tutor_screen.export import orders_csv
from tutor_screen.llm_client import PROVIDERS, test_connection
from tutor_screen.matching import evaluate_order
from tutor_screen.local_parser import LOCAL_FIELDS_REVISION
from tutor_screen.models import Criteria, Order
from tutor_screen.pipeline import TooManyOrders, process_batch, repair_merged_orders
from tutor_screen.history_store import HistoryStoreError, append_event, clear_history, count_imports, create_import, get_import, has_import, list_imports
from tutor_screen.presentation import confirmation_labels


PAGES = ["模型设置", "地图设置", "订单导入", "筛选结果", "开发日志"]
SUBJECT_OPTIONS = ["语文", "数学", "英语", "日语", "理科", "物理", "化学", "生物", "科学", "技术", "文科", "历史", "政治", "地理", "作业辅导"]
DAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


st.set_page_config(page_title="家教筛单器", page_icon="📚", layout="wide", initial_sidebar_state="expanded")
apply_theme()
desktop_layout = desktop_results()


def input_fingerprint(text: str) -> str:
    # 浏览器文本框会把 Windows TXT 的换行归一化，不能因此重新解析同一批。
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def criteria_widget_values(criteria: Criteria) -> dict:
    return {
        "filter_gender": criteria.teacher_gender,
        "filter_grades": criteria.grades.copy(),
        "filter_subjects": criteria.subjects.copy(),
        "filter_mode": criteria.mode,
        "filter_days": [DAY_NAMES[i - 1] for i in criteria.days],
        "filter_frequency": criteria.max_weekly_sessions or "不限",
        "filter_slots": criteria.time_slots.copy(),
        "filter_min_rate": float(criteria.min_hourly_rate or 0),
    }


def init_state() -> None:
    if not st.session_state.get("_model_config_loaded", False):
        try:
            saved = load_settings()
            st.session_state._model_config_error = None
        except ConfigStoreError as exc:
            saved = SavedSettings()
            st.session_state._model_config_error = str(exc)
        st.session_state._saved_model_profiles = saved.profiles
        st.session_state._model_config_loaded = True
        selected_provider = "DeepSeek"
        active = saved.profiles.get(selected_provider)
        st.session_state._last_saved_model_config = active if selected_provider == saved.active_provider else None
        if active is not None and active.provider in PROVIDERS:
            # 优先保留更新代码前已经填在当前会话中的值。
            for key, value in active.model_dump().items():
                st.session_state.setdefault(key, value)
    if st.session_state.get("provider", "DeepSeek") != "DeepSeek":
        # 更新中的旧会话只恢复 DeepSeek 配置，避免沿用其他服务商的密钥。
        profile = st.session_state._saved_model_profiles.get("DeepSeek")
        st.session_state.update(profile.model_dump() if profile is not None else {
            "provider": "DeepSeek", "api_key": "",
            "base_url": PROVIDERS["DeepSeek"][0], "model_name": PROVIDERS["DeepSeek"][1],
        })
        st.session_state._last_saved_model_config = None
    defaults = {
        "page": "订单导入", "provider": "DeepSeek", "api_key": "", "base_url": PROVIDERS["DeepSeek"][0],
        "model_name": PROVIDERS["DeepSeek"][1], "orders": [], "filtered": [], "criteria": Criteria(),
        "input_epoch": 0, "pending_replace": False, "parsed_input_hash": None, "parsed_input_text": None,
        "order_text": "", "uploaded_order_file": None,
        "current_import_id": None, "_history_error": None,
        "pending_clear_history": False, "history_page_index": 0,
        "_history_cleared": False,
        "_local_fields_revision": None,
        "_batch_error": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value
    if not st.session_state.get("_batch_loaded"):
        st.session_state._batch_loaded = True
        if not st.session_state.orders and not st.session_state.order_text and st.session_state.uploaded_order_file is None:
            try:
                saved_batch = batch_store.load_batch()
                if saved_batch:
                    try:
                        import_id = saved_batch.import_id if saved_batch.import_id and has_import(saved_batch.import_id) else None
                    except HistoryStoreError as exc:
                        import_id = None
                        st.session_state._history_error = str(exc)
                    st.session_state.orders = repair_merged_orders(saved_batch.orders)
                    st.session_state.order_text = saved_batch.input_text
                    st.session_state.parsed_input_text = saved_batch.input_text
                    st.session_state.parsed_input_hash = input_fingerprint(saved_batch.input_text)
                    st.session_state.criteria = saved_batch.criteria
                    st.session_state.current_import_id = import_id
                    st.session_state._history_cleared = import_id is None
                    evaluated = [evaluate_order(order, saved_batch.criteria) for order in st.session_state.orders]
                    st.session_state.filtered = map_ui.filter_orders([order for order in evaluated if order.match_status != "已排除"])
                    st.session_state._local_fields_revision = LOCAL_FIELDS_REVISION
                    st.session_state._map_results_dirty = False
                    batch_store.save_batch(saved_batch.model_copy(update={"import_id": import_id, "orders": st.session_state.orders}))
            except (batch_store.BatchStoreError, HistoryStoreError) as exc:
                st.session_state._batch_error = str(exc)
    for key, value in criteria_widget_values(st.session_state.criteria).items():
        st.session_state.setdefault(key, value)


def persist_model_config() -> None:
    config = ModelConfig(
        provider=st.session_state.provider, api_key=st.session_state.api_key.strip(),
        base_url=st.session_state.base_url.strip(), model_name=st.session_state.model_name.strip(),
    )
    if config == st.session_state.get("_last_saved_model_config"):
        return
    if not config.api_key and not st.session_state._saved_model_profiles:
        return
    try:
        saved = save_config(config)
    except ConfigStoreError as exc:
        st.session_state._model_config_error = str(exc)
    else:
        st.session_state._saved_model_profiles = saved.profiles
        st.session_state._last_saved_model_config = config
        st.session_state._model_config_error = None


init_state()
persist_model_config()


def navigation_changed() -> None:
    st.session_state.page = st.session_state.nav_widget


def navigate_to(page: str) -> None:
    st.session_state.page = page


def remember_upload(widget_key: str) -> None:
    uploaded = st.session_state.get(widget_key)
    st.session_state.uploaded_order_file = (
        {"name": uploaded.name, "content": uploaded.getvalue()} if uploaded is not None else None
    )
    if uploaded is not None:
        saved = st.session_state.uploaded_order_file
        try:
            text = saved["content"].decode("utf-8-sig")
            note = ""
        except UnicodeDecodeError:
            text = None
            note = "TXT需为UTF-8编码，原始文件已保留供检查。"
        saved["history_id"] = save_import_record("TXT", text, saved["name"], saved["content"], note)


def remove_upload() -> None:
    st.session_state.uploaded_order_file = None
    st.session_state.input_epoch += 1


def reset_criteria() -> None:
    criteria = Criteria()
    st.session_state.criteria = criteria
    st.session_state.update(criteria_widget_values(criteria))
    map_ui.reset_draft()
    refresh_filtered("重置筛选")


def save_import_record(source_type, text, filename=None, file_content=None, note=""):
    try:
        result = create_import(source_type=source_type, input_text=text, filename=filename, file_content=file_content, note=note)
    except HistoryStoreError as exc:
        st.session_state._history_error = str(exc)
        return None
    st.session_state._history_error = None
    return result


def save_history_event(import_id, kind, *, orders=None, parsed_orders=None, status="已解析", note=""):
    if not import_id:
        return
    try:
        append_event(import_id, kind=kind, criteria=st.session_state.criteria,
                     orders=orders, parsed_orders=parsed_orders, status=status, note=note)
    except HistoryStoreError as exc:
        st.session_state._history_error = str(exc)
    else:
        st.session_state._history_error = None


def refresh_filtered(kind: str) -> None:
    repaired = repair_merged_orders(st.session_state.orders)
    if len(repaired) != len(st.session_state.orders):
        st.session_state.orders = repaired
        st.session_state.pop("_parse_summary", None)
        st.session_state.pop("_parse_failures", None)
    evaluated = [evaluate_order(value, st.session_state.criteria) for value in st.session_state.orders]
    st.session_state.filtered = map_ui.filter_orders([value for value in evaluated if value.match_status != "已排除"])
    save_history_event(st.session_state.current_import_id, kind, orders=evaluated,
                       parsed_orders=st.session_state.orders,
                       note=map_ui.batch_diagnostic_note() if kind == "地图定位更新" else "")
    st.session_state._local_fields_revision = LOCAL_FIELDS_REVISION
    st.session_state._map_results_dirty = False
    persist_current_batch()


def persist_current_batch() -> None:
    if not st.session_state.orders:
        return
    text = st.session_state.parsed_input_text
    if text is None:
        try:
            record = get_import(st.session_state.current_import_id) if st.session_state.current_import_id else None
        except HistoryStoreError:
            record = None
        text = record["input_text"] if record and record["input_text"] is not None else "\n\n".join(order.raw_text for order in st.session_state.orders)
        st.session_state.parsed_input_text = text
    try:
        batch_store.save_batch(batch_store.SavedBatch(
            input_text=text, orders=st.session_state.orders, criteria=st.session_state.criteria,
            import_id=st.session_state.current_import_id,
        ))
    except batch_store.BatchStoreError as exc:
        st.session_state._batch_error = str(exc)
    else:
        st.session_state._batch_error = None


def bootstrap_session_history() -> bool:
    if (not st.session_state.orders or st.session_state.current_import_id or
            st.session_state._history_cleared or st.session_state._history_error):
        return False
    # 旧会话没有上传元数据，明确注明由现存订单片段补存，避免伪称原上传文件。
    import_id = save_import_record("会话补存", "\n\n".join(order.raw_text for order in st.session_state.orders),
                                   note="日志启用前的会话批次，由现存订单片段补存；原上传时间和文件不可恢复。")
    if import_id:
        st.session_state.current_import_id = import_id
        return True
    return False


def confirm_clear_history() -> None:
    try:
        clear_history()
    except HistoryStoreError as exc:
        st.session_state._history_error = str(exc)
        return
    st.session_state.pending_clear_history = False
    st.session_state.current_import_id = None
    st.session_state.history_page_index = 0
    st.session_state._history_error = None
    st.session_state._history_cleared = True
    st.session_state._history_notice = "开发日志已清空。当前订单、筛选条件和模型配置保留。"
    if st.session_state.uploaded_order_file is not None:
        st.session_state.uploaded_order_file.pop("history_id", None)
    persist_current_batch()
    for key in ("history_import_selector", "history_event_selector", "history_order_selector"):
        st.session_state.pop(key, None)


def label(value: object, fallback: str = "待确认") -> str:
    return str(value) if value not in (None, "", []) else fallback


with st.sidebar:
    sidebar_brand()
    st.session_state.nav_widget = st.session_state.page
    page = st.radio("页面", PAGES, key="nav_widget", on_change=navigation_changed, label_visibility="collapsed", width="stretch")
    with st.container(key="sidebar_note"):
        st.caption("v1.0.2 · 本地解析与筛选")
        st.caption("导入与筛选记录保存在本机，可在开发日志中回看。")
    if st.session_state._history_error:
        st.warning("开发日志未保存或读取失败：" + st.session_state._history_error)
    if st.session_state._batch_error:
        st.warning(st.session_state._batch_error)


def import_page() -> None:
    st.title("订单导入")
    st.caption("把零散订单整理成清晰卡片，轻松找到适合自己的家教。")
    if not st.session_state.api_key.strip():
        st.info("尚未配置 API Key。请先到左侧“模型设置”填写；配置会自动加密保存在本机，下次启动自动加载。")
    if st.session_state._model_config_error:
        st.warning(st.session_state._model_config_error)
    with st.container(border=True, key="import_editor"):
        st.subheader("添加订单")
        st.caption("粘贴文本或上传 TXT，再点击下方“应用筛选”。每批最多 300 条订单。")
        left, right = st.columns([1.4, 1], gap="large")
        with left:
            pasted = st.text_area("粘贴订单文本", placeholder="在这里粘贴一批订单，订单之间建议空一行…", height=280, key="order_text", persist_state="session")
        with right:
            upload_key = f"upload_{st.session_state.input_epoch}"
            st.file_uploader("上传 TXT 文件", type=["txt"], key=upload_key, on_change=remember_upload, args=(upload_key,))
            saved_file = st.session_state.uploaded_order_file
            if saved_file is not None:
                st.caption(f"已暂存文件：{saved_file['name']}。切换页面后仍可继续使用。")
                st.button("移除上传文件", key="remove_upload", on_click=remove_upload)
            st.caption("支持 UTF-8 编码的 TXT 文件。建议先移除原文中的个人敏感信息。")
            st.markdown("**处理后可以查看**")
            st.caption("科目与年级、报酬、时间安排，以及每条订单的匹配情况。原始内容可在订单卡片和开发日志中回看。")
        if st.session_state.orders:
            st.warning(f"当前已有 {len(st.session_state.orders)} 条订单。重新导入会覆盖本批结果。")
    draft_criteria, apply = criteria_form()
    if not apply and not st.session_state.pending_replace:
        return
    saved_file = st.session_state.uploaded_order_file
    upload_bytes = saved_file["content"] if saved_file is not None else b""
    upload_text = ""
    if upload_bytes:
        try:
            upload_text = upload_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            st.error("TXT 文件需为 UTF-8 编码。")
            return
    if pasted.strip() and upload_text.strip():
        st.error("粘贴文本和 TXT 文件都有内容。请清空其中一种来源，再开始处理。")
        return
    text = upload_text if upload_text.strip() else pasted
    if saved_file is not None and not text.strip():
        save_history_event(saved_file.get("history_id"), "导入未开始", status="未开始", note="上传文本为空。")
        st.error("请先粘贴订单文本或上传 TXT 文件。")
        return
    input_hash = input_fingerprint(text)
    same_input = st.session_state.parsed_input_hash in {input_hash, hashlib.sha256(text.strip().encode("utf-8")).hexdigest()}
    if st.session_state.orders and st.session_state.parsed_input_hash is None:
        # 兼容升级前的批次；订单仍在时，原输入未变就只做本地筛选。
        same_input = text.split() == "\n".join(order.raw_text for order in st.session_state.orders).split()
        if not same_input and st.session_state.current_import_id:
            try:
                record = get_import(st.session_state.current_import_id)
                same_input = bool(record and text.strip() == (record["input_text"] or "").strip())
            except HistoryStoreError:
                pass
    needs_parse = bool(text.strip()) and (not st.session_state.orders or not same_input)
    if not needs_parse:
        st.session_state.pending_replace = False
        if not map_ui.apply_draft():
            st.warning(st.session_state.map_error)
            return
        st.session_state.criteria = draft_criteria
        refresh_filtered("应用筛选")
        if st.session_state.orders:
            st.session_state.page = "筛选结果"
        st.rerun()
    if st.session_state.orders:
        st.session_state.pending_replace = True
        st.info("确认覆盖上一批订单后开始处理。")
        yes, no = st.columns(2)
        confirmed = yes.button("确认覆盖并处理", type="primary")
        if no.button("取消覆盖"):
            st.session_state.pending_replace = False
            st.rerun()
        if not confirmed:
            return
    st.session_state.pending_replace = False
    if not map_ui.apply_draft():
        st.warning(st.session_state.map_error)
        return
    if upload_text.strip():
        import_id = saved_file.get("history_id")
        if not import_id:
            import_id = save_import_record("TXT", text, saved_file["name"], upload_bytes)
            saved_file["history_id"] = import_id
    else:
        import_id = save_import_record("粘贴", text)
    if not st.session_state.api_key.strip():
        save_history_event(import_id, "导入未开始", status="未开始", note="模型尚未配置，解析未开始。")
        st.error("请先到“模型设置”填写 API Key，再开始解析。")
        return
    if not st.session_state.model_name.strip() or not st.session_state.base_url.strip():
        save_history_event(import_id, "导入未开始", status="未开始", note="模型地址或名称尚未配置，解析未开始。")
        st.error("请先在“模型设置”填写 Base URL 和模型名称。")
        return
    st.session_state.criteria = draft_criteria
    st.session_state.pop("_parse_summary", None)
    st.session_state.pop("_parse_failures", None)
    status = st.status("正在处理订单…", expanded=True)
    progress_text = st.empty()
    bar = st.progress(0)
    ignored_segments = 0
    def update(stage: str, current: int, total: int) -> None:
        nonlocal ignored_segments
        if stage == "已忽略非订单内容":
            ignored_segments = current
        elif stage == "逐条解析":
            fraction = current / total if total else 0.0
            progress_text.text(f"正在解析订单：{current}/{total}（{fraction:.0%}）" if total else "没有可解析的订单")
            bar.progress(fraction)
        else:
            status.write(f"{stage}" + (f"：{current}/{total}" if total else ""))
            if stage == "订单边界识别完成":
                progress_text.text(f"正在解析订单：0/{total}（0%）" if total else "没有可解析的订单")
    try:
        save_history_event(import_id, "开始解析", status="处理中")
        orders = process_batch(text, st.session_state.api_key, st.session_state.base_url, st.session_state.model_name, update)
    except TooManyOrders as exc:
        save_history_event(import_id, "解析未完成", status="未完成", note="超过300条订单上限，上一批保留。")
        status.update(label="处理未开始", state="error")
        st.error(str(exc))
        return
    except Exception:
        save_history_event(import_id, "解析未完成", status="未完成", note="批次处理异常，原输入与上一批结果保留。")
        status.update(label="处理失败", state="error")
        st.error("批次处理异常。原输入与上一批结果已保留，可在开发日志核对导入记录。")
        return
    if not orders:
        progress_text.text("没有可解析的订单")
        if ignored_segments:
            st.info(f"已忽略非订单内容 {ignored_segments} 段")
        save_history_event(import_id, "解析完成", status="无订单", note="输入未识别到订单，原文保留。")
        status.update(label="未识别到订单", state="error")
        st.warning("未识别到订单。输入可能只有宣传文字或批次标题，请补充订单正文。")
        return
    st.session_state.orders = orders
    st.session_state.parsed_input_hash = input_hash
    st.session_state.parsed_input_text = text
    st.session_state.map_batch = None
    st.session_state.map_selected_order = ""
    st.session_state.map_order_candidates = []
    st.session_state.current_import_id = import_id
    st.session_state._history_cleared = False
    refresh_filtered("解析完成")
    failures = [order for order in orders if order.extraction_source == "failed"]
    summary = f"解析完成：共 {len(orders)} 条，成功 {len(orders) - len(failures)} 条，失败 {len(failures)} 条"
    if ignored_segments:
        summary += f"；已忽略非订单内容 {ignored_segments} 段"
    progress_text.text(summary)
    bar.progress(1.0)
    # 完成后仍自动进入结果页，在该页保留汇总和 100% 进度。
    st.session_state._parse_summary = summary
    st.session_state._parse_failures = [f"{order.order_number or order.order_id}：{order.extraction_note or '解析失败，原文已保留供复核。'}" for order in failures]
    status.update(label="处理完成", state="complete")
    st.session_state.page = "筛选结果"
    st.rerun()


def criteria_form() -> tuple[Criteria, bool]:
    # 即时暂存输入；只有点击“应用筛选”才更新结果。
    with st.container(border=True, key="filter_panel"):
        st.subheader("筛选条件")
        st.caption("点击“应用筛选”处理新增订单或更新已有结果。同一条件内多选满足任一即可。")
        row1 = st.columns(4)
        gender = row1[0].selectbox("教师性别", ["不限", "男", "女"], key="filter_gender", persist_state="session")
        grades = row1[1].multiselect("年级", ["小学", "初中", "高中"], key="filter_grades", placeholder="全部年级", persist_state="session")
        subjects = row1[2].multiselect("科目", SUBJECT_OPTIONS, key="filter_subjects", placeholder="全部科目", persist_state="session")
        mode = row1[3].selectbox("上课方式", ["不限", "线上", "线下"], key="filter_mode", persist_state="session")
        row2 = st.columns(4)
        days = row2[0].multiselect("可接星期", DAY_NAMES, key="filter_days", placeholder="不限星期", persist_state="session")
        frequency = row2[1].selectbox("每周最多次数", ["不限", 1, 2, 3, 4, 5, 6, 7], key="filter_frequency", persist_state="session")
        slots = row2[2].multiselect("可接时间段", ["上午", "下午", "晚上"], key="filter_slots", placeholder="不限时段", persist_state="session")
        min_rate = row2[3].number_input("最低时薪（元/小时）", min_value=0.0, step=10.0, key="filter_min_rate", persist_state="session")
        map_ui.filter_widgets()
        with st.container(horizontal=True, horizontal_alignment="right"):
            st.button("重置条件", key="reset_filters", on_click=reset_criteria)
            apply = st.button("应用筛选", key="apply_filters", type="primary")
    draft = Criteria(
        teacher_gender=gender, grades=grades, subjects=subjects, mode=mode,
        days=[DAY_NAMES.index(day) + 1 for day in days],
        max_weekly_sessions=None if frequency == "不限" else frequency,
        time_slots=slots, min_hourly_rate=min_rate or None,
    )
    return draft, apply


def order_card(order: Order, *, desktop: bool = False) -> None:
    with st.container(border=True, key=f"order_card_{order.order_id}"):
        with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
            st.markdown(f"**{label(order.grade)} · {label('、'.join(order.subjects))}**　`{order.order_number or order.order_id}`")
            st.badge(order.match_status, color="green" if order.match_status == "明确匹配" else "orange")
        # 兼容代码更新前保存在当前会话内的订单对象。
        rate_min = getattr(order, "hourly_rate_min", None)
        rate_max = getattr(order, "hourly_rate_max", None)
        if order.hourly_rate is not None:
            rate = f"¥{order.hourly_rate:g}/小时"
        elif rate_min is not None and rate_max is not None:
            rate = f"¥{rate_min:g}–{rate_max:g}/小时"
        else:
            rate = label(order.price_text)
        if desktop:
            st.markdown(f"**{rate}** · {order.mode}")
        else:
            st.markdown(f"### {rate}")
            st.caption(order.mode)
        day_options = getattr(order, "day_options", [])
        schedule_options = getattr(order, "schedule_options", [])
        if schedule_options:
            days = " 或 ".join("、".join(DAY_NAMES[i - 1] for i in option.days) + (" " + "、".join(option.time_slots) if option.time_slots else "") for option in schedule_options)
        else:
            days = " 或 ".join("、".join(DAY_NAMES[i - 1] for i in option) for option in day_options) if day_options else "、".join(DAY_NAMES[i - 1] for i in order.days)
        frequency = f"每周 {order.weekly_sessions} 次" if order.weekly_sessions else "次数待确认"
        lesson_hours = getattr(order, "lesson_hours", None)
        if lesson_hours:
            frequency += f" · 每次 {lesson_hours:g} 小时"
        schedule_source = order.field_sources.get("schedule", "")
        slot_label = "时段待协商" if "可协商" in schedule_source else "时段待确认"
        st.caption(f"{label(days, '星期待确认')} / {frequency} / {label('、'.join(order.time_slots), slot_label)}")
        windows = getattr(order, "schedule_windows", [])
        for window in windows:
            window_days = "、".join(DAY_NAMES[i - 1] for i in window.days) or "星期待确认"
            start, end = window.start_minute, window.end_minute
            time_label = "可选时间" if schedule_options or day_options else "上课时间"
            st.caption(f"{time_label}：{window_days} {start // 60:02d}:{start % 60:02d}–{end // 60:02d}:{end % 60:02d}")
        excluded_times = getattr(order, "excluded_times", [])
        if excluded_times:
            st.caption("排除时段：" + "；".join(excluded_times) + "。具体可接时间需确认。")
        if getattr(order, "price_incomplete", False) and rate_min is not None:
            st.caption("薪资范围包含可换算的报价，其他报价仍需确认。")
        st.caption(f"地址：{label(order.address, '未提供')}　｜　教师性别要求：{order.teacher_gender}")
        map_ui.render_card_location(order, compact=desktop)
        st.write("待确认条件：" + ("、".join(confirmation_labels(order.uncertain_fields)) or "无"))
        with st.expander("查看原始订单", expanded=False,
                         key=f"order_raw_{'desktop' if desktop else 'standard'}_{order.order_id}"):
            if not desktop:
                st.markdown("**原始订单**")
            st.text(order.raw_text)


def render_parse_summary() -> None:
    if st.session_state.get("_parse_summary"):
        st.empty().text(st.session_state._parse_summary)
        st.progress(1.0)
        if st.session_state.get("_parse_failures"):
            with st.expander("查看失败详情", expanded=False):
                st.text("\n".join(st.session_state._parse_failures))


def result_page() -> None:
    desktop = desktop_layout
    st.title("筛选结果")
    st.caption("查看订单分布与筛选结果。条件可在“订单导入”页调整，重新筛选不会再次请求模型。")
    if not desktop:
        render_parse_summary()
    map_ui.ensure_state()
    # 先预留地图与订单区域；桌面端并排，手机端沿用上下顺序。
    with st.container(key="results_workspace"):
        map_slot = st.container(border=True, key="results_map")
        list_slot = st.container(key="results_list")
    if bootstrap_session_history():
        refresh_filtered("会话补存")
    if st.session_state.orders and st.session_state._local_fields_revision != LOCAL_FIELDS_REVISION:
        refresh_filtered("字段规则升级")
    elif st.session_state.orders and st.session_state.get("_map_results_dirty"):
        refresh_filtered("地图位置更新")
    orders: list[Order] = st.session_state.filtered
    base_orders = [o for o in (evaluate_order(value, st.session_state.criteria) for value in st.session_state.orders) if o.match_status != "已排除"]
    map_ui.sync_auto_batch(base_orders)
    counts = {status: sum(o.match_status == status for o in orders) for status in ["明确匹配", "可能匹配", "无法解析"]}
    map_orders = [o for o in orders if o.match_status in {"明确匹配", "可能匹配"}]
    with map_slot:
        city = map_ui.current_city() or st.session_state.map_settings.default_city
        origin = map_ui.applied_origin()
        with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
            st.markdown(f"**{city or '订单地图'}**")
            st.caption(f"当前出发点：{origin.label or '已保存地点'}" if origin else "尚未设置出发点")
        credentials = st.session_state.map_settings.credentials
        if credentials.js_key and credentials.security_code:
            map_ui.render_map_panel(map_orders)
        else:
            st.html('<div class="results-map-empty"><span class="map-empty-icon" aria-hidden="true">◎</span>'
                    '<strong>地图尚未配置</strong><p>在“地图设置”填写凭据后，可在这里查看订单分布。</p>'
                    '<small>你可以在“订单导入”调整筛选条件，并继续查看订单列表。</small></div>')
        located = sum(map_ui.point_for(order) is not None for order in map_orders)
        st.caption(f"本批已解析 {len(st.session_state.orders)} 单 · 当前保留 {len(orders)} 单 · 地图显示 {located} 单 · 未落点订单仍保留在列表")
        st.caption("房屋图标：出发点　绿色实线：明确匹配　橙色虚线：可能匹配。“约”表示大致定位，具体地点待确认。筛选后自动定位新地址，使用缓存避免重复查询。")
        if desktop:
            render_parse_summary()
        map_ui.render_toolbar(orders, lambda: refresh_filtered("地图定位更新"))
        with st.container(horizontal=True, horizontal_alignment="left", wrap=False, key="results_actions"):
            st.button("修改筛选条件", key="edit_filters", on_click=navigate_to, args=("订单导入",))
            st.download_button("导出当前筛选 CSV", orders_csv(orders), file_name="家教订单_当前筛选.csv", mime="text/csv", on_click="ignore")
            st.download_button("导出明确匹配 CSV", orders_csv([o for o in orders if o.match_status == "明确匹配"]), file_name="家教订单_明确匹配.csv", mime="text/csv", on_click="ignore")
    with list_slot:
        st.subheader("订单列表")
        st.caption(f"本批 {len(st.session_state.orders)} 条 · 当前保留 {len(orders)} 条 · 已排除 {len(st.session_state.orders) - len(orders)} 条。无法解析订单保留供复核。")
        if not st.session_state.orders:
            st.info("还没有订单。请到“订单导入”粘贴文本或上传 TXT 文件。")
            return
        # 上方同步的自动队列也包含距离尚未知的保留订单。
        labels = [f"{status}（{counts[status]}）" for status in counts]
        selected = next((o for o in orders if o.order_id == st.session_state.map_selected_order), None)
        selected_label = f"{selected.match_status}（{counts[selected.match_status]}）" if selected else None
        focus_token = st.session_state.get("_map_focus_token", 0)
        if selected_label and focus_token != st.session_state.get("_results_tab_focus"):
            st.session_state.results_group = selected_label
        elif st.session_state.get("results_group") not in labels:
            st.session_state.results_group = labels[0]
        st.session_state._results_tab_focus = focus_token
        tabs = st.tabs(labels, key="results_group", on_change="rerun")
        for tab, status in zip(tabs, counts):
            with tab:
                group = [o for o in orders if o.match_status == status]
                if not group:
                    st.info("这里暂时没有订单。可查看“可能匹配”或调整筛选条件。" if status == "明确匹配" else "当前没有这类订单。")
                with st.container(key=f"results_orders_{status}"):
                    for order in group:
                        order_card(order, desktop=desktop)


def settings_page() -> None:
    st.title("模型设置")
    st.caption("连接 DeepSeek API，开始解析订单。配置自动加密保存在本机。")
    left, right = st.columns([2, 1], gap="large")
    with left:
        with st.container(border=True, key="model_connection"):
            st.subheader("连接配置")
            st.caption("填写 DeepSeek API 密钥与模型。")
            st.selectbox("服务商", list(PROVIDERS), key="provider", disabled=True, persist_state="session")
            st.text_input("API Key", type="password", key="api_key", persist_state="session")
            st.text_input("Base URL", key="base_url", persist_state="session")
            st.text_input("模型名称", key="model_name", placeholder="填写 DeepSeek 模型 ID", persist_state="session")
            if st.session_state._model_config_error:
                st.error(st.session_state._model_config_error)
            elif st.session_state.api_key.strip():
                st.caption("当前模型配置已自动保存到本机，重新运行程序后会恢复。")
            else:
                st.caption("填写 API Key 后自动保存 DeepSeek 配置。")
            if st.button("测试连接", key="test_connection", type="primary"):
                if not all([st.session_state.api_key.strip(), st.session_state.base_url.strip(), st.session_state.model_name.strip()]):
                    st.error("请填写 API Key、Base URL 和模型名称。")
                else:
                    with st.spinner("正在测试…"):
                        try:
                            test_connection(st.session_state.api_key, st.session_state.base_url, st.session_state.model_name)
                            st.success("连接成功，模型已返回文本。")
                        except Exception:
                            st.error("连接失败。请检查 DeepSeek 地址、模型名称、密钥和网络。")

    with right:
        with st.container(border=True, key="model_help"):
            st.subheader("填写说明")
            st.markdown("**01 · 快速创建deepseek API**")
            st.markdown("[前往 DeepSeek 创建 API Key](https://platform.deepseek.com/api_keys)")
            st.markdown("**02 · 填写密钥与模型**")
            st.caption("API Key 来自 DeepSeek 控制台；模型名称填写 DeepSeek 提供的模型 ID。")
            st.markdown("**03 · 测试连接**")
            st.caption("连接成功后，前往订单导入页粘贴文本或上传 TXT。")
            st.divider()
            st.caption("配置自动加密保存在本机。密钥以密码显示，不会写入 CSV 或开发日志。")


def history_page() -> None:
    st.title("开发日志")
    st.caption("导入原文、上传文件与解析/筛选快照保存在本机，重启后仍可查看。每条记录独立保留，只有确认清空日志才会删除。")
    if notice := st.session_state.pop("_history_notice", None):
        st.success(notice)
    try:
        total = count_imports()
        max_page = max(0, (total - 1) // 50)
        st.session_state.history_page_index = min(max(0, st.session_state.history_page_index), max_page)
        records = list_imports(limit=50, offset=st.session_state.history_page_index * 50)
    except HistoryStoreError as exc:
        st.error(str(exc))
        return
    with st.container(border=True, key="history_overview"):
        summary, description = st.columns([1, 3], vertical_alignment="center")
        summary.metric("导入记录", total)
        description.markdown("**每次导入，都有记录可回看**")
        description.caption("按批次查看原文、解析结果和筛选快照。记录保存在本机，重启后仍可查看。")
    if st.button("清空全部开发日志", key="clear_history", disabled=total == 0):
        st.session_state.pending_clear_history = True
    if st.session_state.pending_clear_history:
        st.warning("确认永久删除全部导入内容、上传文件和开发日志？当前订单、筛选条件及模型配置会保留。")
        yes, no = st.columns(2)
        yes.button("确认清空日志", key="confirm_clear_history", type="primary", on_click=confirm_clear_history)
        if no.button("取消清空日志", key="cancel_clear_history"):
            st.session_state.pending_clear_history = False
            st.rerun()
    if not records:
        st.info("还没有开发日志。上传TXT或提交粘贴订单后，将自动记录。")
        return
    with st.container(border=True, key="history_record"):
        st.subheader("导入记录")
        previous, next_page = st.columns(2)
        if previous.button("上一页日志", key="history_previous", disabled=st.session_state.history_page_index == 0):
            st.session_state.history_page_index -= 1
            st.rerun()
        if next_page.button("下一页日志", key="history_next", disabled=st.session_state.history_page_index == max_page):
            st.session_state.history_page_index += 1
            st.rerun()
        st.caption(f"第 {st.session_state.history_page_index + 1}/{max_page + 1} 页，每页50条，全部历史记录保留。")
        options = [record["id"] for record in records]
        labels = {record["id"]: f"{record['created_at'][:19].replace('T', ' ')} · {record['source_type']} · {record['filename'] or '粘贴文本'} · {record['status']} · {record['id'][:6]}" for record in records}
        if st.session_state.get("history_import_selector") not in options:
            st.session_state.history_import_selector = options[0]
        selected = st.selectbox("选择导入记录", options, format_func=labels.get, key="history_import_selector")
        try:
            record = get_import(selected)
        except HistoryStoreError as exc:
            st.error(str(exc))
            return
        if record is None:
            st.info("这条日志已被清空。")
            return
        st.caption(f"导入时间：{record['created_at']} · 来源：{record['source_type']} · 状态：{record['status']}")
        if record["filename"]:
            st.text(f"文件名：{record['filename']} · 字节数：{record['file_size']} · SHA256：{record['file_sha256']}")
        if record["note"]:
            st.info(record["note"])
        with st.expander("本次导入原文", expanded=False):
            st.text(record["input_text"] if record["input_text"] is not None else "文件编码不可读取，原始字节已保存。")
        if record["file_content"] is not None:
            st.download_button("下载原始TXT", record["file_content"], file_name=record["filename"] or "orders.txt", mime="text/plain", on_click="ignore")
        export_record = {key: value for key, value in record.items() if key != "file_content"}
        st.download_button("下载此记录JSON", json.dumps(export_record, ensure_ascii=False, indent=2).encode("utf-8"), file_name=f"导入日志_{record['id'][:8]}.json", mime="application/json", on_click="ignore")
    events = record["events"]
    if not events:
        st.info("上传记录已保存，尚未进行解析。")
        return
    with st.container(border=True, key="history_snapshot"):
        st.subheader("操作快照")
        event_ids = [event["id"] for event in events]
        by_id = {event["id"]: event for event in events}
        if st.session_state.get("history_event_selector") not in event_ids:
            st.session_state.history_event_selector = event_ids[-1]
        event_id = st.selectbox("选择操作快照", event_ids, format_func=lambda value: f"{by_id[value]['created_at'][:19]} · {by_id[value]['kind']} · #{value}", key="history_event_selector")
        event = by_id[event_id]
        st.markdown("**筛选条件**")
        st.json(event["criteria"], expanded=False)
        if event["note"]:
            st.info(event["note"])
        orders = event["orders"]
        if not orders:
            st.info("该次操作没有订单快照，可查看导入原文和操作说明。")
            return
        st.caption(f"本次快照 {len(orders)} 条，其中排除 {sum(order['match_status'] == '已排除' for order in orders)} 条。")
        indexes = list(range(len(orders)))
        if st.session_state.get("history_order_selector") not in indexes:
            st.session_state.history_order_selector = 0
        index = st.selectbox("选择订单日志", indexes, format_func=lambda value: f"{orders[value]['order_id']} · {orders[value].get('grade') or '年级未知'} · {orders[value]['match_status']}", key="history_order_selector")
        order = orders[index]
        st.markdown("**原始订单**")
        st.text(order["raw_text"])
        st.markdown("**结构化字段**")
        st.json({key: value for key, value in order.items() if key not in {"raw_text", "field_sources", "match_reason", "extraction_source", "extraction_note"}}, expanded=False)
        st.markdown("**字段来源**")
        st.json(order.get("field_sources", {}), expanded=False)
        st.write("本地匹配理由：" + order.get("match_reason", ""))
        st.write("提取方式：" + {"model": "模型", "local": "本地规则", "failed": "失败"}.get(order.get("extraction_source"), "未知"))
        if order.get("extraction_note"):
            st.text("解析说明：" + order["extraction_note"])
        if event["parsed_orders"]:
            with st.expander("解析阶段完整快照"):
                st.json(event["parsed_orders"], expanded=False)


{"订单导入": import_page, "筛选结果": result_page, "模型设置": settings_page, "地图设置": map_ui.settings_page, "开发日志": history_page}[page]()
