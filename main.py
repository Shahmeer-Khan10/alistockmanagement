import streamlit as st
import sqlite3
import pandas as pd
from datetime import date

DB_NAME = "inventory.db"

# =============== DB Helpers ===============
def get_conn():
    return sqlite3.connect(DB_NAME, check_same_thread=False)

def init_db():
    con = get_conn()
    cur = con.cursor()
    cur.executescript("""
    PRAGMA foreign_keys = ON;

    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        category TEXT,
        brand TEXT,
        unit TEXT DEFAULT 'pcs',
        cost_price REAL DEFAULT 0,
        sale_price REAL DEFAULT 0,
        active INTEGER DEFAULT 1,
        notes TEXT
    );

    CREATE TABLE IF NOT EXISTS parties (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        type TEXT CHECK(type IN ('supplier','customer')) NOT NULL,
        name TEXT NOT NULL,
        phone TEXT,
        address TEXT
    );

    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER NOT NULL,
        txn_type TEXT CHECK(txn_type IN ('IN','OUT','ADJUST')) NOT NULL,
        qty REAL NOT NULL,
        unit_price REAL,
        party_id INTEGER,
        ref_no TEXT,
        txn_date TEXT NOT NULL,
        remarks TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE,
        FOREIGN KEY(party_id) REFERENCES parties(id) ON DELETE SET NULL
    );
    """)
    con.commit()
    con.close()

def get_items_df(active_only=True):
    con = get_conn()
    q = "SELECT * FROM items" + (" WHERE active=1" if active_only else "")
    df = pd.read_sql_query(q, con)
    con.close()
    return df

def get_inventory_df():
    con = get_conn()
    q = """
    SELECT 
        i.id, i.name, i.category, i.brand, i.unit,
        i.cost_price, i.sale_price,
        COALESCE(SUM(
            CASE 
                WHEN t.txn_type='IN' THEN t.qty
                WHEN t.txn_type='OUT' THEN -t.qty
                WHEN t.txn_type='ADJUST' THEN t.qty
                ELSE 0
            END
        ), 0) AS stock_qty
    FROM items i
    LEFT JOIN transactions t ON t.item_id = i.id
    WHERE i.active=1
    GROUP BY i.id
    ORDER BY i.name;
    """
    df = pd.read_sql_query(q, con)
    con.close()
    if not df.empty:
        df["stock_value_cost"] = (df["stock_qty"] * df["cost_price"]).round(2)
    return df

def upsert_party(party_type:str, name:str, phone:str=None, address:str=None):
    if not (name or "").strip():
        return None
    con = get_conn()
    cur = con.cursor()
    cur.execute("SELECT id FROM parties WHERE type=? AND name=?;", (party_type, name.strip()))
    row = cur.fetchone()
    if row:
        pid = row[0]
    else:
        cur.execute("INSERT INTO parties(type,name,phone,address) VALUES(?,?,?,?)",
                    (party_type, name.strip(), phone, address))
        pid = cur.lastrowid
        con.commit()
    con.close()
    return pid

def list_parties(party_type:str):
    con = get_conn()
    df = pd.read_sql_query("SELECT id, name FROM parties WHERE type=? ORDER BY name;", con, params=(party_type,))
    con.close()
    if df.empty:
        df = pd.DataFrame(columns=["id","name"])
    return df

def add_item(**kwargs):
    con = get_conn()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO items(name, category, brand, unit, cost_price, sale_price, notes)
        VALUES(:name, :category, :brand, :unit, :cost_price, :sale_price, :notes)
    """, kwargs)
    con.commit()
    con.close()

def update_item_basic(item_id:int, cost_price:float=None, sale_price:float=None):
    con = get_conn()
    cur = con.cursor()
    cur.execute("""
        UPDATE items 
           SET cost_price = COALESCE(?, cost_price),
               sale_price = COALESCE(?, sale_price)
         WHERE id=?;
    """, (cost_price, sale_price, item_id))
    con.commit()
    con.close()

def record_txn(item_id:int, txn_type:str, qty:float, unit_price:float=None, party_id:int=None,
               ref_no:str=None, txn_date:str=None, remarks:str=None):
    if qty <= 0:
        raise ValueError("Qty must be > 0")
    con = get_conn()
    cur = con.cursor()
    cur.execute("""
        INSERT INTO transactions(item_id, txn_type, qty, unit_price, party_id, ref_no, txn_date, remarks)
        VALUES(?,?,?,?,?,?,?,?)
    """, (item_id, txn_type, qty, unit_price, party_id, ref_no, txn_date or date.today().isoformat(), remarks))
    con.commit()
    con.close()

# =============== UI ===============
st.set_page_config(page_title="Ali Mobile Repairing Center - Inventory", page_icon="📱", layout="wide")
st.title("📱 Ali Mobile Repairing Center - Stock Management")

init_db()

with st.sidebar:
    st.markdown("### Filters")
    items_df_side = get_items_df()
    categories = ["All"] + (sorted(items_df_side["category"].dropna().unique().tolist()) if not items_df_side.empty else [])
    selected_cat = st.selectbox("Category", categories, index=0)
    st.markdown("---")
    st.caption("Use tabs to manage items and stock.")

tab_dash, tab_items, tab_in, tab_out, tab_inv, tab_parties = st.tabs(
    ["Dashboard", "Items", "Stock IN (Purchase)", "Stock OUT (Sale/Issue)", "Inventory & Reports", "Parties"]
)

# =============== Dashboard ===============
with tab_dash:
    inv_df = get_inventory_df()
    if inv_df is None or inv_df.empty:
        st.info("No items yet. Please add items in the 'Items' tab.")
    else:
        inv_filtered = inv_df if selected_cat == "All" else inv_df[inv_df["category"].fillna("") == selected_cat]
        total_items = len(inv_filtered)
        total_qty = float(inv_filtered["stock_qty"].sum()) if total_items else 0
        total_value = float(inv_filtered["stock_value_cost"].sum()) if total_items else 0

        c1, c2, c3 = st.columns(3)
        c1.metric("Total Items", total_items)
        c2.metric("Total Qty", f"{total_qty:.0f}")
        c3.metric("Inventory Value (Cost)", f"PKR {total_value:,.0f}")

        st.markdown("#### Quick Search")
        q = st.text_input("Search item (name/brand/category)", "")
        df_show = inv_filtered.copy()
        if q.strip():
            ql = q.strip().lower()
            df_show = df_show[
                df_show.apply(lambda r: any(
                    ql in str(r[col]).lower() for col in ["name","brand","category"]
                ), axis=1)
            ]
        st.dataframe(
            df_show[["name","category","brand","unit","stock_qty","cost_price","sale_price","stock_value_cost"]]
            .rename(columns={"stock_qty":"In Stock","stock_value_cost":"Stock Value"}),
            use_container_width=True,
            hide_index=True
        )

# =============== Items ===============
with tab_items:
    st.subheader("Add New Item")
    with st.form("add_item_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        name = c1.text_input("Item Name*", placeholder="e.g., iPhone 11 Screen")
        category = c2.selectbox("Category", ["Screen", "Battery", "Charger", "Cable", "Speaker", "Mic", "Tempered Glass", "Cover", "Back Panel", "Charging Port", "Tool", "Adhesive/Consumable", "Other"])
        brand = c3.text_input("Brand", placeholder="e.g., Apple / Samsung")
        
        c4, c5, c6 = st.columns(3)
        unit = c4.text_input("Unit", value="pcs")
        cost_price = c5.number_input("Default Cost Price (PKR)", min_value=0.0, value=0.0, step=1.0)
        sale_price = c6.number_input("Default Sale Price (PKR)", min_value=0.0, value=0.0, step=1.0)
        
        notes = st.text_input("Notes", placeholder="Any notes...")
        submitted = st.form_submit_button("Add Item")
        if submitted:
            if not name.strip():
                st.error("Item Name required.")
            else:
                add_item(
                    name=name.strip(), category=category, brand=(brand.strip() or None),
                    unit=(unit.strip() or "pcs"), cost_price=float(cost_price),
                    sale_price=float(sale_price), notes=(notes.strip() or None)
                )
                st.success(f"Item '{name}' added.")

    st.markdown("---")
    st.subheader("Update Price")
    items_df_update = get_items_df()
    if items_df_update.empty:
        st.info("No items to update.")
    else:
        col1, col2, col3 = st.columns(3)
        sel_item_name = col1.selectbox("Select Item", items_df_update["name"].tolist())
        item_row = items_df_update[items_df_update["name"] == sel_item_name].iloc[0]
        
        cost_new = col2.number_input("Cost Price (PKR)", min_value=0.0, value=float(item_row["cost_price"] or 0.0), step=1.0, key=f"cost_{item_row['id']}")
        sale_new = col3.number_input("Sale Price (PKR)", min_value=0.0, value=float(item_row["sale_price"] or 0.0), step=1.0, key=f"sale_{item_row['id']}")
        
        if st.button("Save Price Changes"):
            update_item_basic(item_row['id'], cost_price=float(cost_new), sale_price=float(sale_new))
            st.success("Item price updated.")

# =============== Stock IN ===============
with tab_in:
    st.subheader("Stock IN (Purchase)")
    items_df_in = get_items_df()
    if items_df_in.empty:
        st.info("Please add items first.")
    else:
        with st.form("stock_in_form", clear_on_submit=True):
            c1, c2 = st.columns([2,1])
            item_sel_name = c1.selectbox("Item", items_df_in["name"].tolist())
            item_row = items_df_in[items_df_in["name"] == item_sel_name].iloc[0]
            qty = c2.number_input("Qty", min_value=1.0, value=1.0, step=1.0)

            c3, c4, c5 = st.columns(3)
            supplier_options = list_parties("supplier")
            supplier_name_sel = c3.selectbox("Supplier (optional)", [""] + supplier_options["name"].tolist())
            supplier_new = c4.text_input("Or add new Supplier")
            unit_price = c5.number_input("Unit Cost (PKR)", min_value=0.0, value=float(item_row["cost_price"] or 0.0), step=1.0)
            
            c6, c7, c8 = st.columns(3)
            ref_no = c6.text_input("Bill/Ref No.")
            txn_date = c7.date_input("Date", value=date.today())
            remarks = c8.text_input("Remarks", placeholder="e.g., Purchase from XYZ")
            
            submitted = st.form_submit_button("Add Stock IN")
            if submitted:
                party_id = upsert_party("supplier", supplier_new.strip() or supplier_name_sel.strip())
                record_txn(
                    item_id=int(item_row["id"]), txn_type="IN", qty=float(qty),
                    unit_price=float(unit_price), party_id=party_id, ref_no=ref_no.strip() or None,
                    txn_date=txn_date.isoformat(), remarks=remarks.strip() or None
                )
                st.success("Stock IN recorded.")

# =============== Stock OUT ===============
with tab_out:
    st.subheader("Stock OUT (Sale/Issue)")
    items_df_out = get_items_df()
    if items_df_out.empty:
        st.info("Please add items first.")
    else:
        inv_df_out = get_inventory_df()
        if inv_df_out is None:
            inv_df_out = pd.DataFrame()

        def curr_stock(iid: int) -> float:
            row = inv_df_out[inv_df_out["id"] == iid]
            return float(row["stock_qty"].values[0]) if not row.empty else 0.0

        df_out = items_df_out.copy()
        df_out["in_stock"] = df_out["id"].apply(curr_stock)
        df_out["label"] = df_out.apply(lambda r: f"{r['name']} [In Stock: {int(r['in_stock'])}]", axis=1)
        
        with st.form("stock_out_form", clear_on_submit=True):
            sel_label = st.selectbox("Item", df_out["label"].tolist())
            item_name = sel_label.split(" [In Stock")[0]
            item_row = df_out[df_out["name"] == item_name].iloc[0]
            item_id, avail = int(item_row["id"]), item_row["in_stock"]

            c1, c2, c3 = st.columns(3)
            qty = c1.number_input(f"Qty (Available: {int(avail)})", min_value=1.0, max_value=float(max(1, avail)), value=1.0, step=1.0)
            unit_price = c2.number_input("Unit Sale Price (PKR)", min_value=0.0, value=float(item_row["sale_price"] or 0.0), step=1.0)
            txn_date = c3.date_input("Date", value=date.today())

            c4, c5, c6 = st.columns(3)
            customer_options = list_parties("customer")
            customer_name_sel = c4.selectbox("Customer (optional)", [""] + customer_options["name"].tolist())
            customer_new = c5.text_input("Or add new Customer")
            ref_no = c6.text_input("Invoice/Ref No.")
            remarks = st.text_input("Remarks", placeholder="e.g., Sold to walk-in customer")

            submitted = st.form_submit_button("Add Stock OUT")
            if submitted:
                if qty > avail:
                    st.error("Not enough stock available.")
                else:
                    party_id = upsert_party("customer", customer_new.strip() or customer_name_sel.strip())
                    record_txn(
                        item_id=item_id, txn_type="OUT", qty=float(qty), unit_price=float(unit_price),
                        party_id=party_id, ref_no=ref_no.strip() or None,
                        txn_date=txn_date.isoformat(), remarks=remarks.strip() or None
                    )
                    st.success("Stock OUT recorded.")

# =============== Inventory & Reports ===============
with tab_inv:
    st.subheader("Inventory & Reports")
    inv_df_report = get_inventory_df()
    if inv_df_report is None or inv_df_report.empty:
        st.info("No inventory yet.")
    else:
        st.dataframe(
            inv_df_report.rename(columns={"stock_qty":"In Stock", "stock_value_cost":"Stock Value (Cost)"}),
            use_container_width=True,
            hide_index=True
        )
        st.download_button("Export to CSV", data=inv_df_report.to_csv(index=False), file_name="inventory_export.csv", mime="text/csv")

        st.markdown("#### Transactions (Recent)")
        con = get_conn()
        tx = pd.read_sql_query("""
            SELECT t.txn_date, t.txn_type, i.name AS item, t.qty, t.unit_price, t.ref_no, t.remarks
            FROM transactions t
            JOIN items i ON i.id = t.item_id
            ORDER BY t.created_at DESC
            LIMIT 200;
        """, con)
        con.close()
        st.dataframe(tx, use_container_width=True, hide_index=True)

# =============== Parties ===============
with tab_parties:
    st.subheader("Suppliers & Customers")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("##### Add Supplier")
        with st.form("add_supplier", clear_on_submit=True):
            s_name = st.text_input("Supplier Name")
            s_phone = st.text_input("Phone")
            s_addr = st.text_area("Address")
            if st.form_submit_button("Add Supplier"):
                if s_name.strip():
                    upsert_party("supplier", s_name.strip(), s_phone.strip() or None, s_addr.strip() or None)
                    st.success("Supplier added.")
                else:
                    st.error("Name required.")
        st.markdown("##### Supplier List")
        st.dataframe(list_parties("supplier"), use_container_width=True, hide_index=True)

    with c2:
        st.markdown("##### Add Customer")
        with st.form("add_customer", clear_on_submit=True):
            c_name = st.text_input("Customer Name")
            c_phone = st.text_input("Phone")
            c_addr = st.text_area("Address")
            if st.form_submit_button("Add Customer"):
                if c_name.strip():
                    upsert_party("customer", c_name.strip(), c_phone.strip() or None, c_addr.strip() or None)
                    st.success("Customer added.")
                else:
                    st.error("Name required.")
        st.markdown("##### Customer List")
        st.dataframe(list_parties("customer"), use_container_width=True, hide_index=True)