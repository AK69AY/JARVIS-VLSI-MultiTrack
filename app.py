import importlib.util
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import pandas as pd
import streamlit as st

APP_DIR = Path(__file__).resolve().parent

st.set_page_config(
    page_title="JARVIS-VLSI dashboard",
    page_icon=":material/memory:",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.html(
    """
    <style>
    .block-container h1 {
        background: linear-gradient(90deg, #00FFA3 0%, #4CC9F0 100%);
        -webkit-background-clip: text;
        background-clip: text;
        color: transparent;
        letter-spacing: 0.3px;
    }
    [data-testid="stMetric"] {
        border: 1px solid rgba(0, 255, 163, 0.22);
        border-left: 4px solid #00FFA3;
        border-radius: 10px;
        background: linear-gradient(180deg, rgba(0, 255, 163, 0.07), rgba(0, 255, 163, 0.0));
        padding: 8px 14px;
    }
    [data-testid="stMetricValue"] { color: #00FFA3; }
    [data-baseweb="tab"][aria-selected="true"] {
        border-bottom-color: #00FFA3;
        color: #00FFA3;
    }
    [data-testid="stSidebar"] { border-right: 1px solid rgba(0, 255, 163, 0.20); }
    [data-testid="stCodeBlock"] { border: 1px solid #1E2530; }
    </style>
    """
)

st.session_state.setdefault("sim_result", None)
st.session_state.setdefault("synth_result", None)

RTL_FILES = ["bitnet_ternary_pe.v", "async_fifo_cdc.v"]


@st.cache_data(ttl=120, show_spinner=False)
def read_repo_file(name: str) -> str:
    if not name:
        return ""
    path = APP_DIR / name
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def parse_parameters(text: str) -> dict:
    return {k: int(v) for k, v in re.findall(r"parameter\s+(\w+)\s*=\s*(\d+)", text)}


def parse_vcd(path: Path) -> dict | None:
    if not path.is_file():
        return None
    signals: dict = {}
    timescale = ""
    scope: list = []
    last_time = 0
    with path.open(encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("$timescale"):
                timescale = line.replace("$timescale", "").replace("$end", "").strip()
            elif line.startswith("$scope"):
                parts = line.split()
                if len(parts) >= 3:
                    scope.append(parts[2])
            elif line.startswith("$upscope"):
                if scope:
                    scope.pop()
            elif line.startswith("$var"):
                parts = line.split()
                if len(parts) >= 5:
                    code = parts[3]
                    name = parts[4]
                    signals[code] = {
                        "name": ".".join(scope + [name]),
                        "width": int(parts[2]),
                        "transitions": 0,
                    }
            elif line.startswith("#"):
                try:
                    last_time = int(line[1:])
                except ValueError:
                    pass
            elif line.startswith(("b", "B")):
                parts = line.split()
                if len(parts) == 2 and parts[1] in signals:
                    signals[parts[1]]["transitions"] += 1
            elif line[0] in "01xzXZ" and len(line) >= 2:
                code = line[1:]
                if code in signals:
                    signals[code]["transitions"] += 1
    if not signals:
        return None
    rows = sorted(signals.values(), key=lambda s: -s["transitions"])
    return {
        "timescale": timescale,
        "last_time": last_time,
        "signal_count": len(rows),
        "total_transitions": sum(r["transitions"] for r in rows),
        "signals": rows,
    }


def run_simulation() -> dict:
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, str(APP_DIR / "run_sim.py")],
            cwd=str(APP_DIR),
            capture_output=True,
            text=True,
            timeout=180,
        )
        output = proc.stdout + proc.stderr
        returncode = proc.returncode
    except subprocess.TimeoutExpired:
        output = "[ERROR] run_sim.py timed out after 180s"
        returncode = 124
    elapsed = time.perf_counter() - t0
    m = re.search(r"\$finish called at (\d+)", output)
    return {
        "passed": returncode == 0
        and "[VERIFICATION PASSED]" in output
        and "[VERIFICATION FAILED]" not in output,
        "errors": output.count("[FAIL]"),
        "finish_ps": int(m.group(1)) if m else None,
        "returncode": returncode,
        "output": output,
        "elapsed": elapsed,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def run_yosys() -> dict:
    yosys = shutil.which("yosys")
    if not yosys:
        return {
            "ok": False,
            "log": "yosys executable not found on PATH.\n"
            "Install it (e.g. 'winget install Yosys' or build from https://yosyshq.net/yosys/)\n"
            "then re-run: yosys -s synth.ys",
        }
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            [yosys, "-s", "synth.ys"],
            cwd=str(APP_DIR),
            capture_output=True,
            text=True,
            timeout=300,
        )
        log = proc.stdout + proc.stderr
        ok = proc.returncode == 0 and (APP_DIR / "gate_level_netlist.v").is_file()
    except subprocess.TimeoutExpired:
        log = "[ERROR] yosys timed out after 300s"
        ok = False
    return {"ok": ok, "log": log[-20000:], "elapsed": time.perf_counter() - t0}


CELL_KEYWORDS = {
    "module", "endmodule", "input", "output", "inout", "wire", "assign",
    "always", "initial", "begin", "end", "if", "else", "case", "function",
    "task", "generate", "genvar", "parameter", "localparam", "reg", "integer",
}


def cell_family(cell: str) -> str:
    c = cell.lower()
    rules = [
        ("xnor", "XNOR"),
        ("xor", "XOR"),
        ("nand", "NAND"),
        ("nor", "NOR"),
        ("and", "AND"),
        ("or", "OR"),
        ("mux", "MUX"),
        ("dff", "Flip-flop / latch"),
        ("latch", "Flip-flop / latch"),
        ("aoi", "Complex AOI/OAI"),
        ("oai", "Complex AOI/OAI"),
        ("inv", "Inverter"),
        ("not", "Inverter"),
        ("buf", "Buffer"),
        ("diode", "Diode / tie cell"),
        ("ha", "Half adder"),
        ("fa", "Full adder"),
        ("add", "Adder"),
    ]
    for key, family in rules:
        if key in c:
            return family
    return "Other / combinational"


GATE_EQUIV = {
    "Flip-flop / latch": 4,
    "MUX": 3,
    "Complex AOI/OAI": 2,
    "Full adder": 3,
    "Half adder": 2,
    "Adder": 3,
}


def parse_netlist(text: str) -> pd.DataFrame:
    counts: Counter = Counter()
    pattern = re.compile(r"^\s*(\\?[\w.$\\]+)\s+(?:\\?[\w.$\[\]]+)\s*\(", re.M)
    for match in pattern.finditer(text):
        cell = match.group(1).lstrip("\\")
        if cell in CELL_KEYWORDS:
            continue
        counts[cell] += 1
    rows = []
    for cell, n in counts.items():
        family = cell_family(cell)
        rows.append(
            {
                "Cell": cell,
                "Family": family,
                "Instances": n,
                "Gate equiv.": GATE_EQUIV.get(family, 1) * n,
            }
        )
    df = pd.DataFrame(rows, columns=["Cell", "Family", "Instances", "Gate equiv."])
    if not df.empty:
        df = df.sort_values("Instances", ascending=False).reset_index(drop=True)
    return df


def strip_tex(text: str) -> str:
    text = re.sub(r"\\textbf\{(.*?)\}", r"\1", text, flags=re.S)
    text = re.sub(r"\\(?:emph|textit|texttt)\{(.*?)\}", r"\1", text, flags=re.S)
    text = re.sub(r"\\label\{[^}]*\}", "", text)
    return " ".join(text.split())


def parse_patent(tex: str) -> dict:
    abstract = ""
    m = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", tex, re.S)
    if m:
        abstract = strip_tex(m.group(1))
    claims = [
        strip_tex(c)
        for c in re.findall(r"\\item\s+(.*?)(?=\\item|\\end\{enumerate\})", tex, re.S)
    ]
    return {"abstract": abstract, "claims": claims}


def claim_meta(claim: str, index: int) -> dict:
    dep = re.search(r"claim\s+(\d+)", claim)
    if dep and int(dep.group(1)) != index:
        kind = f"Depends on claim {dep.group(1)}"
    else:
        kind = "Independent"
    subject = " ".join(claim.split()[:10])
    return {"number": index, "kind": kind, "subject": subject + "..."}


with st.sidebar:
    st.markdown("### :material/dashboard: JARVIS-VLSI")
    st.caption("Autonomous multi-track silicon architecture suite")
    st.divider()
    st.markdown("#### PE parameters")
    data_width = st.slider("Data width N (bits)", 4, 32, 8, key="data_width")
    acc_width = st.slider("Accumulator width (bits)", 8, 40, 16, key="acc_width")
    vdd = st.slider("Supply voltage V_DD (V)", 0.4, 1.2, 0.9, 0.05, key="vdd")
    freq = st.slider("Clock frequency (GHz)", 0.1, 5.0, 1.0, 0.1, key="freq")
    st.divider()
    st.markdown("#### Display")
    st.toggle("Show RTL source code", value=True, key="show_source")
    st.caption(
        "Tracks: Aegis-IMC (security) | Ternary-PE (compute) | "
        "Async-FIFO-CDC (interconnect)"
    )

st.title("JARVIS-VLSI multi-track dashboard")

tab1, tab2, tab3, tab4 = st.tabs(
    [
        "RTL Core & BitNet PE",
        "Simulation & CI Runner",
        "Yosys Gate Synthesis",
        "Aegis-CIM Patent Viewer",
    ]
)

with tab1:
    st.subheader("Interactive RTL viewer")
    source_file = st.segmented_control(
        "Source file",
        RTL_FILES,
        default=RTL_FILES[0],
        key="source_file",
    )
    source_text = read_repo_file(source_file or RTL_FILES[0])
    if st.session_state.show_source and source_text:
        st.code(source_text, language="verilog", line_numbers=True, wrap_lines=True)
    elif not source_text:
        st.info(f"{source_file} not found in repository root.")

    st.subheader("Ternary weight encoding")
    encode_df = pd.DataFrame(
        [
            {"in_weight": "2'b01", "W": "+1", "Operation": "Sign-preserving pass-through"},
            {"in_weight": "2'b10", "W": "-1", "Operation": "2's complement negation"},
            {"in_weight": "2'b00", "W": "0", "Operation": "Zero / bypass"},
        ]
    )
    st.dataframe(encode_df, hide_index=True, width="content")
    st.latex(
        r"\text{term} = \begin{cases}"
        r"+X & W = \texttt{2'b01} \\"
        r"-X & W = \texttt{2'b10} \\"
        r"\ \ 0 & W = \texttt{2'b00}"
        r"\end{cases}"
    )
    st.markdown(
        r"Multiplication-free accumulate: $\ \text{Acc}_{out} = \text{Acc}_{in} "
        r"+ (X_i \oplus S_i) + S_i$"
    )

    st.subheader("Parameter calculator")
    with st.form("ternary_calc"):
        st.caption(
            f"Global parameters from sidebar: N = {data_width}, "
            f"ACC = {acc_width}, V_DD = {vdd} V, f = {freq} GHz"
        )
        op_col1, op_col2, op_col3 = st.columns(3)
        with op_col1:
            x_in = st.number_input("Activation X", -128, 127, 12, key="x_in")
        with op_col2:
            w_sel = st.selectbox(
                "Weight W",
                ["+1  (2'b01)", "-1  (2'b10)", "0   (2'b00)"],
                key="w_sel",
            )
        with op_col3:
            acc0 = st.number_input(
                "Accumulator in", -32768, 32767, 0, key="acc0"
            )
        submitted = st.form_submit_button(
            "Accumulate", icon=":material/calculate:", type="primary"
        )
    if submitted:
        term = 0
        if w_sel.startswith("+1"):
            term = x_in
        elif w_sel.startswith("-1"):
            term = -x_in
        st.metric("Accumulator out", acc0 + term, border=True)

    n = data_width
    eta = 1.0 / n
    p_mac_idx = n * n
    p_ter_idx = n
    st.subheader("Dynamic power model")
    st.markdown(
        r"$P_{MAC} = \mathcal{O}(N^2)\,C_{sw}V_{DD}^2 f,"
        r"\quad P_{Ternary} = \mathcal{O}(N)\,C_{sw}V_{DD}^2 f,"
        r"\quad \eta = \dfrac{P_{Ternary}}{P_{MAC}} = \dfrac{1}{N}$"
    )
    with st.container(horizontal=True):
        st.metric(
            "Dynamic power reduction",
            f"{(1 - eta) * 100:.1f}%",
            delta=f"~88% at N=8",
            border=True,
        )
        st.metric("Power ratio eta", f"{eta:.3f}", border=True)
        st.metric("Switching complexity", f"O(N) vs O(N^2)", border=True)
        st.metric(
            "Multiplier cells instantiated", "0", delta="-100%", border=True
        )
    power_df = pd.DataFrame(
        {
            "Architecture": ["N-bit multiplier tree (MAC)", "Ternary PE (add-only)"],
            "Relative dynamic power": [1.0, eta],
        }
    )
    st.bar_chart(power_df, x="Architecture", y="Relative dynamic power")

    st.subheader("Async FIFO (CDC) parameters")
    fifo_params = parse_parameters(read_repo_file("async_fifo_cdc.v"))
    addr_w = fifo_params.get("ADDR_WIDTH", 4)
    data_w = fifo_params.get("DATA_WIDTH", 8)
    with st.container(horizontal=True):
        st.metric("Depth", f"{1 << addr_w} x {data_w} bit", border=True)
        st.metric("Gray pointer width", f"{addr_w + 1} bit", border=True)
        st.metric("Synchronizer stages", "2", border=True)
        st.metric("Toggle per increment", "dH = 1", border=True)
    st.markdown(
        r"Metastability resilience: $\text{MTBF} = "
        r"\dfrac{e^{s \cdot t_{met}}}{\mu \cdot f_{clkA} \cdot f_{clkB}}$"
    )

with tab2:
    st.subheader("Simulation control panel")
    ctrl_col1, ctrl_col2 = st.columns([1, 3])
    with ctrl_col1:
        run_clicked = st.button(
            "Run simulation",
            type="primary",
            icon=":material/play_arrow:",
            width="stretch",
        )
    with ctrl_col2:
        res = st.session_state.sim_result
        if res is None:
            st.caption("No run yet - press 'Run simulation' to execute run_sim.py")
        elif res["passed"]:
            st.badge(
                f"PASS  ({res['timestamp']})",
                color="green",
                icon=":material/task_alt:",
            )
        else:
            st.badge(
                f"FAIL  ({res['timestamp']})",
                color="red",
                icon=":material/error:",
            )

    if run_clicked:
        with st.spinner("Compiling RTL and executing self-checking testbench..."):
            st.session_state.sim_result = run_simulation()
        res = st.session_state.sim_result

    if res is not None:
        with st.container(horizontal=True):
            st.metric(
                "Status", "PASS" if res["passed"] else "FAIL", border=True
            )
            st.metric("Failed checks", res["errors"], border=True)
            st.metric("Exit code", res["returncode"], border=True)
            if res["finish_ps"] is not None:
                st.metric(
                    "Sim end time", f"{res['finish_ps'] / 1000:.0f} ns", border=True
                )
            st.metric("Wall time", f"{res['elapsed']:.2f} s", border=True)

        st.markdown("#### Live output")
        st.code(res["output"].strip(), language="log", wrap_lines=True)

    st.subheader("Cycle waveform summary")
    vcd = parse_vcd(APP_DIR / "sim_output.vcd")
    if vcd is None:
        st.info("No waveform found - run the simulation to generate sim_output.vcd.")
    else:
        with st.container(horizontal=True):
            st.metric("Signals dumped", vcd["signal_count"], border=True)
            st.metric("Value transitions", vcd["total_transitions"], border=True)
            st.metric("Timescale", vcd["timescale"] or "n/a", border=True)
            st.metric("Last timestamp", f"{vcd['last_time']}", border=True)
        wf_df = pd.DataFrame(vcd["signals"])
        st.dataframe(wf_df, hide_index=True, height=240)
        top = wf_df.head(8)[["name", "transitions"]]
        st.bar_chart(top, x="name", y="transitions")

    st.subheader("GitHub CI pipeline")
    ci_text = read_repo_file(".github/workflows/ci.yml")
    if ci_text:
        steps = re.findall(r"- name:\s*(.+)", ci_text)
        runner = re.findall(r"runs-on:\s*(\S+)", ci_text)
        branches = re.findall(r"branches:\s*\[(.*?)\]", ci_text)
        info_col1, info_col2 = st.columns(2)
        with info_col1:
            st.metric("Runner", runner[0] if runner else "n/a", border=True)
            st.metric("Steps", len(steps), border=True)
        with info_col2:
            st.metric(
                "Trigger branches",
                ", ".join(b.strip() for b in branches) if branches else "n/a",
                border=True,
            )
            st.metric(
                "Verification gate",
                "python run_sim.py",
                border=True,
            )
        for i, step in enumerate(steps, 1):
            st.markdown(f"{i}. {step.strip()}")
        with st.expander("Raw ci.yml"):
            st.code(ci_text, language="yaml", line_numbers=True, wrap_lines=True)
    else:
        st.info(".github/workflows/ci.yml not found.")

with tab3:
    st.subheader("Yosys synthesis script")
    synth_text = read_repo_file("synth.ys")
    if synth_text:
        st.code(synth_text, language="text", line_numbers=True)
    else:
        st.info("synth.ys not found.")

    yosys_found = shutil.which("yosys") is not None
    if st.button(
        "Run Yosys synthesis",
        icon=":material/memory:",
        disabled=not yosys_found,
        help=None
        if yosys_found
        else "Yosys executable not detected on PATH",
    ):
        with st.spinner("Synthesizing bitnet_ternary_pe to gate level..."):
            st.session_state.synth_result = run_yosys()
    if not yosys_found:
        st.warning(
            "Yosys is not installed on this machine. Install it, or open "
            "gate_level_netlist.v after synthesizing elsewhere."
        )

    synth_res = st.session_state.synth_result
    if synth_res is not None:
        if synth_res["ok"]:
            st.success(
                f"Synthesis completed in {synth_res.get('elapsed', 0):.2f}s - "
                "gate_level_netlist.v written."
            )
        else:
            st.error("Synthesis failed.")
        with st.expander("Yosys log"):
            st.code(synth_res["log"], language="log", wrap_lines=True)

    netlist_path = APP_DIR / "gate_level_netlist.v"
    if not netlist_path.is_file():
        st.info(
            "gate_level_netlist.v has not been generated yet - run Yosys "
            "synthesis (or 'yosys -s synth.ys' manually) to populate cell counts, "
            "area utilization, and the standard-cell mapping below."
        )
    else:
        net_text = netlist_path.read_text(encoding="utf-8", errors="replace")
        cells_df = parse_netlist(net_text)
        if cells_df.empty:
            st.info("Netlist parsed but no cell instances detected.")
        else:
            total_inst = int(cells_df["Instances"].sum())
            total_ge = int(cells_df["Gate equiv."].sum())
            dff_df = cells_df[cells_df["Family"] == "Flip-flop / latch"]
            with st.container(horizontal=True):
                st.metric("Total cell instances", total_inst, border=True)
                st.metric("Distinct cell types", len(cells_df), border=True)
                st.metric("Gate equivalents", total_ge, border=True)
                st.metric("Sequential cells", int(dff_df["Instances"].sum()), border=True)
            st.markdown("#### Standard-cell mapping")
            st.dataframe(cells_df, hide_index=True, height=300)
            fam = (
                cells_df.groupby("Family", as_index=False)[["Instances", "Gate equiv."]]
                .sum()
                .sort_values("Instances", ascending=False)
            )
            st.markdown("#### Area utilization by cell family")
            st.bar_chart(fam, x="Family", y="Gate equiv.")
            with st.expander("Raw gate-level netlist"):
                st.code(net_text, language="verilog", line_numbers=True, wrap_lines=True)

    pe_params = parse_parameters(read_repo_file("bitnet_ternary_pe.v"))
    with st.expander("Design parameters (source RTL)"):
        pcol1, pcol2 = st.columns(2)
        with pcol1:
            st.metric(
                "bitnet_ternary_pe DATA_WIDTH",
                pe_params.get("DATA_WIDTH", "n/a"),
                border=True,
            )
            st.metric(
                "bitnet_ternary_pe ACC_WIDTH",
                pe_params.get("ACC_WIDTH", "n/a"),
                border=True,
            )
        with pcol2:
            st.metric("async_fifo DATA_WIDTH", data_w, border=True)
            st.metric("async_fifo ADDR_WIDTH", addr_w, border=True)

with tab4:
    patent_tex = read_repo_file("patent_draft.tex")
    if not patent_tex:
        st.info("patent_draft.tex not found.")
    else:
        parsed = parse_patent(patent_tex)
        st.subheader("Abstract")
        if parsed["abstract"]:
            with st.container(border=True):
                st.markdown(parsed["abstract"])
        st.subheader("Claims")
        meta_df = pd.DataFrame(
            [claim_meta(c, i + 1) for i, c in enumerate(parsed["claims"])]
        )
        st.dataframe(meta_df, hide_index=True, width="content")
        for i, claim in enumerate(parsed["claims"], 1):
            meta = claim_meta(claim, i)
            with st.container(border=True):
                header_col1, header_col2 = st.columns([4, 2], vertical_alignment="center")
                with header_col1:
                    st.markdown(f"**Claim {i}**")
                with header_col2:
                    st.caption(meta["kind"])
                st.markdown(claim)
        st.subheader("Full patent document")
        pdf_path = APP_DIR / "patent_draft.pdf"
        if pdf_path.is_file():
            if importlib.util.find_spec("streamlit_pdf"):
                st.pdf(str(pdf_path), height=640)
            else:
                st.warning(
                    "Embedded PDF viewer requires the optional extra: "
                    "`pip install streamlit[pdf]`"
                )
            st.download_button(
                "Download patent_draft.pdf",
                data=pdf_path.read_bytes(),
                file_name="patent_draft.pdf",
                mime="application/pdf",
                icon=":material/download:",
            )
        else:
            st.info("patent_draft.pdf not found - compile patent_draft.tex first.")
