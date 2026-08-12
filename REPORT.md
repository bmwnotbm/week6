# ETL Lab Report

Student ID:
Name:

## 1. Data Quality Problems Found
- **customers.csv**: มี `customer_id` ซ้ำ (C004, C009), `province` เขียนไม่เป็นมาตรฐาน (ไทย/อังกฤษ ปน, ตัวพิมพ์เล็ก-ใหญ่ปน, ตัวย่อ เช่น "BKK", "chon buri", "จันทบุรี"), `email`/`province` บางแถวว่าง
- **orders.csv**: มี `order_id` ซ้ำ (O0011, O0041, O0101 ปรากฏ 2 ครั้ง), `order_date` มี 4 รูปแบบปน (`YYYY/MM/DD`, `DD/MM/YYYY`, `YYYY-MM-DD`, `DD-Mon-YYYY`) และมีค่าที่อ่านไม่ออก (`not-a-date`), `qty` ติดลบ, `unit_price` ติดลบ, `discount_pct` เกิน 100%, `status` ตัวพิมพ์ปน (`PAID`, `paid`), มี `customer_id`/`product_id` ที่ไม่มีอยู่จริงในระบบ (C999, P999)
- **products.json**: โครงสร้างซ้อน (`category.name`, `pricing.price`) ต้อง flatten, `pricing.price` บางค่าเป็น string ที่มี comma (`"1,299.00"`) ไม่ใช่ number, `category.name` บางตัวเป็น `null`

## 2. Cleaning / Transformation Rules
- **Customers**: ลบ `customer_id` ซ้ำด้วย `drop_duplicates(keep="first")`, map `province` ผ่าน dictionary (case-insensitive) → ค่ามาตรฐาน 4 จังหวัด, ค่าที่ map ไม่ได้/ว่าง → `"Unknown"`, `email` ว่าง → `"unknown@example.com"`
- **Products**: flatten ด้วย `pd.json_normalize` แล้ว rename เป็น `category`, `price`, แปลง `price` เป็น numeric (ตัด comma ออกก่อน), `category` ที่เป็น null/ว่าง → `"Unknown"`
- **Orders**:
  - ลบ `order_id` ซ้ำ (`keep="first"`) — แถวที่เหลือถูกส่งไปที่ rejects (reason: `duplicate_order_id`)
  - แปลง `order_date` ด้วยการลองหลาย format ทีละแบบ, ที่แปลงไม่ได้ → reject (`invalid_order_date`)
  - `status` → lowercase ทั้งหมด
  - reject เมื่อ: `qty <= 0`, `unit_price <= 0`, `discount_pct < 0 หรือ > 100`
  - order ที่ status เป็น `pending`/`cancelled` (ข้อมูลถูกต้อง แต่ไม่ใช่ยอดขาย) จะถูกกรองออกจาก sales เฉยๆ **ไม่นับเป็น reject**
- **Merge**: join กับ customers/products ที่ทำความสะอาดแล้ว, order ที่ `customer_id`/`product_id` ไม่พบในระบบ → reject (`unknown_customer` / `unknown_product`)
- **Calculate**: `gross_amount = qty * unit_price`, `discount_amount = gross_amount * discount_pct/100`, `sales_amount = gross_amount - discount_amount`

## 3. Rejected Records
จำนวน: **7 รายการ** (ดู `output/rejects.csv`)

เหตุผลหลัก:
- `duplicate_order_id` — 3 รายการ (O0011, O0041, O0101)
- `invalid_qty` — 1 รายการ (O0007: qty = -2)
- `invalid_discount_pct` — 1 รายการ (O0021: discount_pct = 150)
- `invalid_order_date` — 1 รายการ (O0034: order_date = "not-a-date")
- `invalid_unit_price` — 1 รายการ (O0091: unit_price = -100.0)

(หมายเหตุ: order สถานะ pending/cancelled อีก 76 รายการ **ไม่ถูกนับเป็น reject** เพราะข้อมูลไม่ได้ผิดปกติ เพียงแต่ไม่เข้าเงื่อนไข "paid/completed" จึงไม่ถูกนำไปคำนวณยอดขาย)

## 4. ETL Validation
- Valid transformed rows: **100**
- Warehouse rows: **100**
- Duplicate order_id: **0**
- Source total sales: **192,074.66**
- Warehouse total sales: **192,074.66**
- Validation status: **PASS**

## 5. Idempotency Test
จำนวน fact_sales หลัง run ครั้งที่ 1: **100**

จำนวน fact_sales หลัง run ครั้งที่ 2: **100**

อธิบายผล: `fact_sales` ไม่เพิ่มขึ้นเมื่อรัน pipeline ซ้ำ เพราะตาราง `fact_sales` กำหนดให้ `order_id` เป็น `PRIMARY KEY` และการโหลดข้อมูลใช้คำสั่ง `INSERT OR IGNORE` ดังนั้นเมื่อรันซ้ำ record ที่มี `order_id` เดิมอยู่แล้วจะถูกข้ามไปโดยอัตโนมัติ ส่วน `dim_customer` และ `dim_product` ใช้ `INSERT ... ON CONFLICT DO UPDATE` (upsert) จึงอัปเดตข้อมูลล่าสุดได้โดยไม่สร้างแถวซ้ำเช่นกัน
