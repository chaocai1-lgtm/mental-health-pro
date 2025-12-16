import streamlit as st
from neo4j import GraphDatabase
from pyecharts import options as opts
from pyecharts.charts import Graph, WordCloud, Line, Pie
from streamlit_echarts import st_pyecharts
import time
import random
import json
import uuid 
import datetime
import pandas as pd

# ================= 1. 配置区域 =================
if "NEO4J_URI" in st.secrets:
    URI = st.secrets["NEO4J_URI"]
    AUTH = ("neo4j", st.secrets["NEO4J_PASSWORD"])
    ADMIN_PWD = st.secrets.get("ADMIN_PASSWORD", "admin888") 
else:
    URI = "neo4j+ssc://7eb127cc.databases.neo4j.io"
    AUTH = ("neo4j", "wE7pV36hqNSo43mpbjTlfzE7n99NWcYABDFqUGvgSrk")
    ADMIN_PWD = "admin888"

# ================= 2. 初始化 Session =================
if 'user_id' not in st.session_state:
    st.session_state['user_id'] = str(uuid.uuid4())[:8]
if 'is_admin_logged_in' not in st.session_state:
    st.session_state['is_admin_logged_in'] = False

# ================= 3. 后端逻辑 =================
class GraphApp:
    def __init__(self):
        try:
            self.driver = GraphDatabase.driver(URI, auth=AUTH)
            self.driver.verify_connectivity()
        except Exception as e:
            st.error(f"❌ 数据库连接失败: {e}")

    def close(self):
        if hasattr(self, 'driver'): self.driver.close()

    def get_all_pains(self):
        try:
            with self.driver.session() as session:
                return [r["name"] for r in session.run("MATCH (p:PainPoint) RETURN p.name as name")]
        except:
            return []

    def get_diagnosis_data(self, selected_pains):
        with self.driver.session() as session:
            pain_details = session.run("MATCH (p:PainPoint) WHERE p.name IN $names RETURN p.name as name, p.symptoms as symptoms", names=selected_pains).data()
            mechs = session.run("MATCH (p:PainPoint)-[:EXPLAINED_BY]->(m:Mechanism) WHERE p.name IN $names RETURN DISTINCT m.name as name, m.desc as desc, m.origin as origin", names=selected_pains).data()
            # 注意：这里必须获取 json 格式的 sections 和 cases
            modules = session.run("MATCH (p:PainPoint)-[:SOLVED_BY]->(c:Module) WHERE p.name IN $names RETURN DISTINCT c.id as id, c.title as title, c.topic as topic, c.summary as summary, c.quote as quote, c.sections as sections_json, c.cases as cases_json ORDER BY c.id ASC", names=selected_pains).data()
            methods = session.run("""
                MATCH (p:PainPoint)-[:USE_TOOL]->(t:Method)
                WHERE p.name IN $names
                RETURN DISTINCT t.name as name, t.desc as desc, t.step as step, t.scene as scene, coalesce(t.likes, 0) as likes
                ORDER BY likes DESC
            """, names=selected_pains).data()
            graph = session.run("MATCH (p:PainPoint) WHERE p.name IN $names MATCH (p)-[r]-(target) RETURN p, r, target", names=selected_pains).graph()
            return pain_details, mechs, modules, methods, graph

    def log_user_search(self, user_id, selected_pains):
        if not selected_pains: return
        sim_duration = random.randint(3, 20) 
        risk_level = "高危" if any(x in str(selected_pains) for x in ["自杀", "抑郁", "死亡", "绝望"]) else random.choice(["一般", "关注"])
        with self.driver.session() as session:
            query = """
            MERGE (u:Student {uid: $uid})
            CREATE (l:SearchLog {timestamp: datetime(), date: date(), duration: $duration, risk_level: $risk})
            MERGE (u)-[:PERFORMED]->(l)
            WITH l
            MATCH (p:PainPoint) WHERE p.name IN $pains
            MERGE (l)-[:SEARCHED]->(p)
            """
            session.run(query, uid=user_id, pains=selected_pains, duration=sim_duration, risk=risk_level)

    def upvote_method(self, method_name):
        with self.driver.session() as session:
            query = "MATCH (m:Method {name: $name}) SET m.likes = coalesce(m.likes, 0) + 1 RETURN m.likes as new_count"
            result = session.run(query, name=method_name).single()
            return result["new_count"] if result else 0

    def get_dashboard_filtered_data(self, days_range):
        with self.driver.session() as session:
            kpis = session.run("""MATCH (l:SearchLog) WHERE l.date >= date() - duration({days: $days}) RETURN count(l) as total_visits, avg(coalesce(l.duration, 5)) as avg_duration, sum(CASE WHEN l.risk_level = '高危' THEN 1 ELSE 0 END) as high_risk_count""", days=days_range).single()
            trend = session.run("""MATCH (l:SearchLog) WHERE l.date >= date() - duration({days: $days}) RETURN toString(l.date) as date, count(l) as count ORDER BY date ASC""", days=days_range).data()
            top_pains = session.run("""MATCH (l:SearchLog)-[:SEARCHED]->(p:PainPoint) WHERE l.date >= date() - duration({days: $days}) RETURN p.name as name, count(l) as value ORDER BY value DESC LIMIT 30""", days=days_range).data()
            risk_dist = session.run("""MATCH (l:SearchLog) WHERE l.date >= date() - duration({days: $days}) RETURN l.risk_level as name, count(l) as value""", days=days_range).data()
            top_methods = session.run("""MATCH (m:Method) WHERE m.likes IS NOT NULL RETURN m.name as name, m.likes as value ORDER BY value DESC LIMIT 10""").data()
            return kpis, trend, top_pains, risk_dist, top_methods

# ================= 4. 可视化与辅助函数 =================
def build_line_chart(trend_data):
    if not trend_data: return None
    x, y = [d['date'] for d in trend_data], [d['count'] for d in trend_data]
    return Line().add_xaxis(x).add_yaxis("访问量", y, is_smooth=True, areastyle_opts=opts.AreaStyleOpts(opacity=0.3, color="#00cc96")).set_global_opts(title_opts=opts.TitleOpts(title="访问趋势"), xaxis_opts=opts.AxisOpts(boundary_gap=False))

def build_pie_chart(risk_data):
    if not risk_data: return None
    return Pie().add("", risk_data, radius=["40%", "70%"]).set_colors(["#ff4b4b", "#ffa15a", "#00cc96"]).set_global_opts(title_opts=opts.TitleOpts(title="风险分布"))

def build_wordcloud(data):
    return WordCloud().add("", data, word_size_range=[20, 80])

def build_graph_chart(graph_data):
    if not graph_data: return None
    nodes, links, seen = [], [], set()
    categories = [{"name": "困扰", "itemStyle": {"color": "#ff4b4b"}}, {"name": "课程", "itemStyle": {"color": "#00cc96"}}, {"name": "原理", "itemStyle": {"color": "#636efa"}}, {"name": "工具", "itemStyle": {"color": "#ffa15a"}}]
    cat_map = {"PainPoint":0, "Module":1, "Mechanism":2, "Method":3}
    for node in graph_data.nodes:
        if node.element_id in seen: continue
        seen.add(node.element_id)
        label = list(node.labels)[0]
        nodes.append({"name": node.get("name") or node.get("title"), "symbolSize": 30 if label=="PainPoint" else 20, "category": cat_map.get(label, 0), "label": {"show": True}})
    for rel in graph_data.relationships:
        links.append({"source": rel.start_node.get("name") or rel.start_node.get("title"), "target": rel.end_node.get("name") or rel.end_node.get("title"), "value": rel.type})
    return Graph(init_opts=opts.InitOpts(height="500px")).add("", nodes, links, categories=categories, repulsion=4000).set_global_opts(title_opts=opts.TitleOpts(title="归因图谱"))

def ai_generate_report(pain_details, mechs, methods):
    symptoms = [p['symptoms'].split('、')[0] for p in pain_details if p['symptoms']]
    sym_text = f"（如 {symptoms[0]} 等）" if symptoms else ""
    return f"### 🤖 AI 心理诊断书\n同学你好，AI 已收到你的反馈。你提到的这些感受{sym_text}，其实是成长的信号。"

def generate_text_report(time_label, kpis, top_pains):
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"【心理态势报告】\n时间：{now_str}\n周期：{time_label}\n总人次：{kpis['total_visits'] if kpis else 0}\nTop5热点：\n" + (chr(10).join([f"{i+1}. {p['name']}" for i, p in enumerate(top_pains[:5])]) if top_pains else "无")

# ================= 5. 主程序入口 =================
st.set_page_config(page_title="心理导学系统 Pro", layout="wide", page_icon="🧠")

# ✨【修复】添加 CSS 样式，确保卡片和工具栏好看
st.markdown("""
<style>
    .card {background:#f9f9f9; padding:20px; border-radius:10px; margin-bottom:15px; border-left:5px solid #00cc96}
    .mech-card {background:#eef2ff; padding:15px; border-radius:10px; margin-bottom:10px; border-left:5px solid #636efa}
    .tool-card {background:#fff8e1; padding:20px; border-radius:10px; border:1px solid #ffe082; margin-bottom:15px;}
    .quote {font-family: serif; font-style: italic; color: #666; margin: 10px 0; padding-left:10px; border-left:3px solid #ccc;}
</style>
""", unsafe_allow_html=True)

app = GraphApp()

with st.sidebar:
    st.title("心理学导学系统")
    view_mode = st.radio("视图模式：", ["👨‍🎓 学生/访客模式", "👩‍🏫 教师/管理模式"])
    if view_mode == "👩‍🏫 教师/管理模式":
        if not st.session_state['is_admin_logged_in']:
            pwd = st.text_input("请输入管理密码：", type="password")
            if st.button("🔐 确认登录"):
                if pwd == ADMIN_PWD:
                    st.session_state['is_admin_logged_in'] = True
                    st.rerun()
                else: st.error("密码错误")
        else:
            st.success("✅ 管理员在线")
            if st.button("退出登录"):
                st.session_state['is_admin_logged_in'] = False; st.rerun()

# ================= 后台 =================
if view_mode == "👩‍🏫 教师/管理模式" and st.session_state['is_admin_logged_in']:
    st.title("📊 校园心理健康态势感知")
    col_filter, col_export = st.columns([3, 1])
    with col_filter:
        time_options = {"近 7 天": 7, "近 1 个月": 30, "近 3 个月": 90, "近 6 个月": 180, "近 1 年": 365}
        label = st.pills("📅 分析周期", list(time_options.keys()), selection_mode="single", default="近 7 天") or "近 7 天"
        days = time_options[label]
    
    kpis, trend, top_pains, risk_dist, top_methods = app.get_dashboard_filtered_data(days)
    
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("访问人次", kpis['total_visits'] if kpis else 0)
    k2.metric("高危预警", kpis['high_risk_count'] if kpis else 0, delta_color="inverse")
    k3.metric("平均停留", f"{kpis['avg_duration']:.1f}m" if kpis and kpis['avg_duration'] else "0")
    k4.metric("热点Focus", top_pains[0]['name'] if top_pains else "无")
    
    st.divider()
    c1, c2 = st.columns([2, 1])
    with c1: st.subheader(f"📈 趋势 ({label})"); st_pyecharts(build_line_chart(trend), height="350px") if trend else st.info("无数据")
    with c2: st.subheader("⚠️ 风险"); st_pyecharts(build_pie_chart(risk_dist), height="350px") if risk_dist else st.info("无数据")
    
    c3, c4 = st.columns(2)
    with c3: st.subheader("🔥 词云"); st_pyecharts(build_wordcloud(top_pains), height="400px") if top_pains else st.info("无数据")
    with c4: 
        st.subheader("🏆 点赞榜")
        if top_methods: st.bar_chart({"方案": [x['name'] for x in top_methods], "赞": [x['value'] for x in top_methods]}, x="方案", y="赞", color="#ffa15a", horizontal=True)

    st.markdown("---")
    st.subheader("📥 导出")
    c_ex1, c_ex2 = st.columns(2)
    with c_ex1:
        if st.button("📄 生成简报"):
            rpt = generate_text_report(label, kpis, top_pains)
            st.text_area("", rpt, height=200); st.download_button("下载 .txt", rpt, f"report_{datetime.date.today()}.txt")
    with c_ex2:
        st.write("📊 原始数据")
        if trend: st.download_button("下载趋势 .csv", pd.DataFrame(trend).to_csv(index=False).encode('utf-8-sig'), "trend.csv")

# ================= 前台 (核心修复部分) =================
else:
    if view_mode == "👩‍🏫 教师/管理模式": st.warning("请先登录")
    else:
        st.title("🎓 大学生心理健康 · 智能导学系统")
        c1, c2 = st.columns([3, 1])
        with c1: all_pains = app.get_all_pains(); selected = st.multiselect("🔍 你遇到了什么问题？", all_pains)
        with c2: st.write(""); st.write(""); start = st.button("🚀 开始分析", type="primary", use_container_width=True)

        if start and selected:
            app.log_user_search(st.session_state['user_id'], selected)
            with st.spinner("AI 分析中..."):
                pain_details, mechs, modules, methods, graph = app.get_diagnosis_data(selected)
                
                # 1. AI 诊断书
                st.success(ai_generate_report(pain_details, mechs, methods))
                
                # ✨【修复】2. 深度归因 (Mechanism) 展示
                if mechs:
                    st.subheader("🧠 深度归因")
                    for m in mechs:
                        st.markdown(f"""
                        <div class="mech-card">
                            <h4>{m['name']} ({m['origin']})</h4>
                            <p>{m['desc']}</p>
                        </div>
                        """, unsafe_allow_html=True)

                # ✨【修复】3. 深度学习路径 (Modules) - 包含折叠详情
                if modules:
                    st.subheader("🗺️ 深度学习路径")
                    for mod in modules:
                        try:
                            sections = json.loads(mod['sections_json']) if mod['sections_json'] else []
                            cases = json.loads(mod['cases_json']) if mod['cases_json'] else []
                        except: sections, cases = [], []
                        
                        st.markdown(f"""
                        <div class="card">
                            <h3>📍 {mod['title']}：{mod['topic']}</h3>
                            <div class="quote">“{mod['quote']}”</div>
                            <p>{mod['summary']}</p>
                        </div>
                        """, unsafe_allow_html=True)
                        
                        # 恢复折叠框
                        with st.expander(f"📚 查看 {mod['title']} 的详细知识点与案例", expanded=False):
                            ec1, ec2 = st.columns(2)
                            with ec1:
                                st.markdown("#### 📖 核心知识点")
                                for sec in sections:
                                    st.markdown(f"**{sec['title']}**")
                                    st.caption(sec['content'])
                            with ec2:
                                st.markdown("#### 🎬 经典案例")
                                if cases:
                                    for case in cases:
                                        st.markdown(f"**{case['name']}**")
                                        st.caption(case['description'])
                                else: st.caption("本章侧重理论，暂无案例")

                # 4. 推荐工具
                if methods:
                    st.subheader("🛠️ 推荐工具")
                    cols = st.columns(3)
                    for i, m in enumerate(methods):
                        with cols[i % 3]:
                            with st.container():
                                st.markdown(f"""
                                <div class="tool-card">
                                    <h4>💊 {m['name']}</h4>
                                    <p style="font-size:14px; color:#666">{m['scene']}</p>
                                    <p>{m['desc']}</p>
                                </div>
                                """, unsafe_allow_html=True)
                                if st.button(f"❤️ 觉得有用 ({m['likes']})", key=f"l_{m['name']}"):
                                    app.upvote_method(m['name']); st.rerun()
                                with st.expander("👉 操作步骤"):
                                    st.write(m['step'])

                # 5. 图谱
                if graph:
                    st.divider(); st.subheader("🕸️ 归因图谱"); st_pyecharts(build_graph_chart(graph), height="500px")

app.close()

