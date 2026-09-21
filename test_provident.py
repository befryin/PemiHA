"""Standalone Diagnostic Script for Provident Energy (MeterConnex).

Run this script to test authentication and inspect the raw API responses:
    python3 test_provident.py
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
import getpass
import json
import sys

import httpx

DEFAULT_BASE_URL = "https://provident.meterconnex.com"
DEFAULT_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://provident.meterconnex.com",
    "Referer": "https://provident.meterconnex.com/secure/Dashboard/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",
}


async def run_diagnostics(username: str, password: str, base_url: str = DEFAULT_BASE_URL) -> None:
    print(f"\n[*] Testing connection to {base_url} for user: {username} ...\n")

    async with httpx.AsyncClient(base_url=base_url, headers=DEFAULT_HEADERS, timeout=30.0, follow_redirects=True) as client:
        # 1. Login
        print("--- 1. Testing ProcessLogin ---")
        login_resp = await client.post(
            "/login/LoginService.aspx/ProcessLogin",
            json={"username": username, "password": password, "rememberMe": False},
        )
        print(f"Status: {login_resp.status_code}")
        print(f"Cookies after login: {dict(client.cookies)}")
        print(f"Raw Response: {login_resp.text}\n")

        if ".ASPXAUTH" not in client.cookies:
            print("[!] ERROR: .ASPXAUTH cookie not received. Login failed.")
            return

        # 2. Crucial Step: GET /secure/Dashboard/ to initialize ASP.NET Session state & meter registry
        print("--- 2. Initializing ASP.NET Session State via GET /secure/Dashboard/ ---")
        dash_resp = await client.get("/secure/Dashboard/")
        print(f"Dashboard Page GET Status: {dash_resp.status_code}")
        print(f"Cookies after Dashboard GET: {dict(client.cookies)}")
        print(f"Dashboard Page URL: {dash_resp.url}")
        print(f"Dashboard HTML size: {len(dash_resp.text)} bytes\n")

        # 3. Get Utilities
        print("--- 3. Testing GetUtilities ---")
        util_resp = await client.post("/secure/Dashboard/Default.aspx/GetUtilities", json={})
        print(f"Status: {util_resp.status_code}")
        print(f"Raw Response: {util_resp.text}\n")

        try:
            util_data = util_resp.json()
            utilities = util_data.get("d", [])
            if isinstance(utilities, str):
                utilities = json.loads(utilities)
        except Exception as err:
            print(f"[!] Failed to parse utilities: {err}")
            utilities = ["Cooling", "Electricity", "EV", "Heating", "Hot Water"]

        print(f"Discovered Utilities: {utilities}\n")

        today = date.today()
        yesterday = today - timedelta(days=1)
        first_of_month = date(today.year, today.month, 1)
        first_of_year = date(today.year, 1, 1)

        # 4. Test Meter Tree & QuickGraphs (Parking Spot Breakdown)
        print("--- 4. Testing Meter Tree & QuickGraphs for Spot-Level Breakdown ---")
        try:
            # Initialize QuickGraphs session
            qg_page = await client.get("/secure/QuickGraphs.aspx")
            print(f"QuickGraphs Page GET Status: {qg_page.status_code}")

            # A. Root Nodes
            print("\n[A] Meter Tree Root Nodes (/api/internal/metertree/rootnodes?depth=2):")
            tree_resp = await client.get(
                "/api/internal/metertree/rootnodes?depth=2",
                headers={"Referer": f"{base_url}/secure/QuickGraphs.aspx"},
            )
            print(f"Status: {tree_resp.status_code}")
            try:
                tree_json = tree_resp.json()
                print("Root Nodes JSON:", json.dumps(tree_json, indent=2))
            except Exception:
                print("Raw Root Nodes:", tree_resp.text)
                tree_json = []

            # B. Check for groups and fetch children
            group_ids = []
            meter_ids = []

            def find_nodes(node_list):
                if isinstance(node_list, dict):
                    node_list = [node_list]
                for n in node_list:
                    nid = str(n.get("id") or n.get("Id") or "")
                    ntype = str(n.get("type") or n.get("Type") or "").lower()
                    if "group" in ntype or n.get("hasChildren") or n.get("HasChildren") or (nid.isdigit()):
                        clean_gid = nid.replace("GROUP:", "").replace("group:", "")
                        if clean_gid and clean_gid not in group_ids:
                            group_ids.append(clean_gid)
                    elif nid and nid.lower() != "root":
                        meter_ids.append(nid)

                    children = n.get("children") or n.get("Children") or []
                    if isinstance(children, list) and children:
                        find_nodes(children)

            if isinstance(tree_json, list):
                find_nodes(tree_json)

            print(f"\nDiscovered Group IDs: {group_ids}")
            print(f"Discovered Meter IDs: {meter_ids}")

            for gid in group_ids:
                if gid and gid.lower() != "root":
                    print(f"\n[B] Child Nodes for Group {gid} (/api/internal/metertree/getchildren?groupId={gid}):")
                    c_resp = await client.get(
                        f"/api/internal/metertree/getchildren?groupId={gid}",
                        headers={"Referer": f"{base_url}/secure/QuickGraphs.aspx"},
                    )
                    print(f"Status: {c_resp.status_code}")
                    try:
                        c_json = c_resp.json()
                        print(f"Group {gid} Children JSON:", json.dumps(c_json, indent=2))
                        if isinstance(c_json, list):
                            for cn in c_json:
                                cn_id = str(cn.get("id") or cn.get("Id") or "")
                                if cn_id and cn_id not in meter_ids:
                                    meter_ids.append(cn_id)
                    except Exception:
                        print("Raw Children:", c_resp.text)

            # C. Query QuickGraphs with aggregateGroups=false
            all_meters_str = ",".join(dict.fromkeys(meter_ids + [f"GROUP:{g}" for g in group_ids]))
            if not all_meters_str:
                all_meters_str = "MP:857718,GROUP:171467,MP:585550,MP:857717,MP:579487,MP:579552,MP:579659,MP:579724"

            print(f"\n[C] Querying QuickGraphs (/api/internal/graphs/quickgraphs) with aggregateGroups=false:")
            print(f"Meterlist: {all_meters_str}")
            qg_resp = await client.get(
                "/api/internal/graphs/quickgraphs",
                params={
                    "meterlist": all_meters_str,
                    "startDate": yesterday.strftime("%Y-%m-%d"),
                    "endDate": today.strftime("%Y-%m-%d"),
                    "aggregateGroups": "false",
                },
                headers={"Referer": f"{base_url}/secure/QuickGraphs.aspx"},
            )
            print(f"Status: {qg_resp.status_code}")
            try:
                qg_data = qg_resp.json()
                print("QuickGraphs (Yesterday Breakdown) JSON:", json.dumps(qg_data, indent=2)[:2500])
            except Exception:
                print("Raw QuickGraphs Response:", qg_resp.text[:2500])

        except Exception as err:
            print(f"[!] Meter tree / QuickGraphs error: {err}\n")

        # 5. Test Each Utility
        for u in utilities:
            print(f"==================================================")
            print(f" TESTING UTILITY: {u}")
            print(f"==================================================")

            # A. UpdateCard (30 days)
            print(f"\n[A] UpdateCard (period: 30) for {u}:")
            card_resp = await client.post(
                "/secure/Dashboard/Default.aspx/UpdateCard",
                json={"utility": u, "period": 30},
            )
            print(f"Status: {card_resp.status_code}")
            print(f"Raw Response: {card_resp.text}")

            # B. GetChartData - Year
            print(f"\n[B] GetChartData (period: 'year', start: '{first_of_year}') for {u}:")
            year_resp = await client.post(
                "/secure/Dashboard/Default.aspx/GetChartData",
                json={"utility": u, "period": "year", "start": first_of_year.strftime("%Y-%m-%d")},
            )
            print(f"Status: {year_resp.status_code}")
            print(f"Raw Response: {year_resp.text}")

            # C. GetChartData - Month
            print(f"\n[C] GetChartData (period: 'month', start: '{first_of_month}') for {u}:")
            month_resp = await client.post(
                "/secure/Dashboard/Default.aspx/GetChartData",
                json={"utility": u, "period": "month", "start": first_of_month.strftime("%Y-%m-%d")},
            )
            print(f"Status: {month_resp.status_code}")
            print(f"Raw Response: {month_resp.text}")

            # D. GetChartData - Past Days Hourly Breakdown
            print(f"\n[D] Historical Hourly Breakdown (Past 7 Days) for {u}:")
            for day_offset in range(1, 8):
                past_d = today - timedelta(days=day_offset)
                d_resp = await client.post(
                    "/secure/Dashboard/Default.aspx/GetChartData",
                    json={"utility": u, "period": "day", "start": past_d.strftime("%Y-%m-%d")},
                )
                print(f" - {past_d.strftime('%Y-%m-%d')}: Status {d_resp.status_code}, Raw: {d_resp.text}")

            # E. GetChartData - Day (Today)
            print(f"\n[E] GetChartData (period: 'day', start: '{today}') for {u}:")
            today_resp = await client.post(
                "/secure/Dashboard/Default.aspx/GetChartData",
                json={"utility": u, "period": "day", "start": today.strftime("%Y-%m-%d")},
            )
            print(f"Status: {today_resp.status_code}")
            print(f"Raw Response: {today_resp.text}\n")


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        user = sys.argv[1]
        pwd = sys.argv[2]
        url = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_BASE_URL
    else:
        print("--- Provident Diagnostics Login ---")
        user = input("Username / Account ID: ").strip()
        pwd = getpass.getpass("Password: ")
        url = input(f"Portal URL [{DEFAULT_BASE_URL}]: ").strip() or DEFAULT_BASE_URL

    asyncio.run(run_diagnostics(user, pwd, url))
