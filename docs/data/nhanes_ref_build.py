"""Weighted percentiles of a few measures for US adults (all, and non-Hispanic Asian), NHANES 2017-Mar 2020 + 2021-2023.

Builds the table in backend/buddy/nhanesref.py. Run once, offline, with pandas and numpy (not needed on the server):
  1. download into an empty folder, from https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2017/DataFiles/ the files
     P_DEMO P_BMX P_BPXO P_GHB P_TCHOL P_HDL P_GLU P_TRIGLY, and from .../Public/2021/DataFiles/ the files
     DEMO_L BMX_L BPXO_L GHB_L TCHOL_L HDL_L GLU_L TRIGLY_L (each NAME.xpt);
  2. python -I nhanes_ref_build.py <that folder> nhanes_pct.json
  3. turn the JSON into the REF dict of nhanesref.py (one line per group).
"""
import json, sys
import numpy as np, pandas as pd
d = sys.argv[1]
r = lambda n: pd.read_sas(f"{d}/{n}.xpt", format="xport", encoding=None)
P = [5, 10, 25, 50, 75, 90, 95]
YEARS = {"P": 3.2, "L": 2.0}
TOTAL = sum(YEARS.values())


def cycle(tag):
    pre = "P_" if tag == "P" else ""
    suf = "" if tag == "P" else "_L"
    n = lambda base: f"{pre}{base}{suf}"
    demo = r(n("DEMO"))[["SEQN", "RIAGENDR", "RIDAGEYR", "RIDRETH3", "WTMECPRP" if tag == "P" else "WTMEC2YR"]]
    demo.columns = ["SEQN", "sex", "age", "eth", "wmec"]
    bmx = r(n("BMX"))[["SEQN", "BMXBMI", "BMXWAIST"]]
    bp = r(n("BPXO"))
    bp["sbp"] = bp[["BPXOSY1", "BPXOSY2", "BPXOSY3"]].mean(axis=1)
    bp["dbp"] = bp[["BPXODI1", "BPXODI2", "BPXODI3"]].mean(axis=1)
    ghb = r(n("GHB"))
    ghb_w = "WTPH2YR" if tag == "L" else None
    tc, hdl = r(n("TCHOL")), r(n("HDL"))
    glu, tri = r(n("GLU")), r(n("TRIGLY"))
    fw = "WTSAF2YR" if tag == "L" else "WTSAFPRP"
    x = demo.merge(bmx, on="SEQN", how="left").merge(bp[["SEQN", "sbp", "dbp"]], on="SEQN", how="left")
    x = x.merge(ghb[["SEQN", "LBXGH"] + ([ghb_w] if ghb_w else [])], on="SEQN", how="left")
    x = x.merge(tc[["SEQN", "LBXTC"]], on="SEQN", how="left").merge(hdl[["SEQN", "LBDHDD"]], on="SEQN", how="left")
    x = x.merge(glu[["SEQN", "LBXGLU", fw]].rename(columns={fw: "wfast"}), on="SEQN", how="left")
    x = x.merge(tri[["SEQN", "LBDLDLM"]], on="SEQN", how="left")
    x["wlab"] = x[ghb_w] if ghb_w else x["wmec"]  # 2021-2023 blood tests carry a phlebotomy weight
    if tag == "L":
        tcw = tc[["SEQN", "WTPH2YR"]].rename(columns={"WTPH2YR": "wtc"})
        x = x.merge(tcw, on="SEQN", how="left")
    else:
        x["wtc"] = x["wmec"]
    scale = YEARS[tag] / TOTAL
    for w in ["wmec", "wlab", "wtc", "wfast"]:
        x[w] = x[w] * scale
    return x


x = pd.concat([cycle("P"), cycle("L")], ignore_index=True)
x = x[x.age >= 20]
MEAS = {"bmi": ("BMXBMI", "wmec"), "waist_cm": ("BMXWAIST", "wmec"), "systolic": ("sbp", "wmec"), "diastolic": ("dbp", "wmec"),
        "a1c": ("LBXGH", "wlab"), "total_chol": ("LBXTC", "wtc"), "hdl": ("LBDHDD", "wtc"),
        "fasting_glucose": ("LBXGLU", "wfast"), "ldl": ("LBDLDLM", "wfast")}


def wpct(v, w):
    o = np.argsort(v); v, w = v[o], w[o]
    c = (np.cumsum(w) - w / 2) / w.sum()
    return [round(float(np.interp(p / 100, c, v)), 1) for p in P]


BANDS = {"20-39": (20, 39), "40-59": (40, 59), "60+": (60, 200), "all": (20, 200)}
out = {}
for grp, gsel in {"asian": x.eth == 6, "all": x.eth > 0}.items():
    for sx, ssel in {"male": x.sex == 1, "female": x.sex == 2, "any": x.sex > 0}.items():
        for band, (lo, hi) in BANDS.items():
            sel = x[gsel & ssel & (x.age >= lo) & (x.age <= hi)]
            row = {}
            for m, (col, wcol) in MEAS.items():
                s = sel[[col, wcol]].dropna()
                s = s[s[wcol] > 0]
                if len(s) >= 30:
                    row[m] = {"n": int(len(s)), "p": wpct(s[col].to_numpy(), s[wcol].to_numpy())}
            out[f"{grp}|{sx}|{band}"] = row
json.dump(out, open(sys.argv[2], "w"), separators=(",", ":"))
for k in ["asian|male|40-59", "asian|female|20-39", "all|male|40-59"]:
    print(k, {m: (v["n"], v["p"][3]) for m, v in out[k].items()})
