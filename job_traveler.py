# job_traveler.py
import json
import os

def get_int(prompt):
    while True:
        raw = input(prompt).strip()
        try:
            return int(raw)
        except ValueError:
            print("Invalid number. Enter a whole number.")

def save_job(job):
    os.makedirs("jobs", exist_ok=True)

    filename = f"jobs/{job['job_number']}.json"

    with open(filename, "w") as file:
        json.dump(job, file, indent=4)

    print(f"\nSaved job traveler to {filename}")


job_number = input("Job Number: ").strip()
customer = input("Customer: ").strip()
part_number = input("Part Number: ").strip()
description = input("Description: ").strip()
qty_to_make = get_int("Qty To Make: ")
material = input("Material: ").strip()
cut_length = input("Cut Length: ").strip()

job = {
    "job_number": job_number,
    "customer": customer,
    "part_number": part_number,
    "description": description,
    "qty_to_make": qty_to_make,
    "material": material,
    "cut_length": cut_length,
    "programming": {},
    "saw_cutting": {},
    "cnc_machining": {},
    "deburr": {},
    "inspection": {},
    "packing": {},
    "shipping": {}
   
    
}

save_job(job)

print("\n" + "=" * 50)
print("JOB TRAVELER")
print("=" * 50)

print(f"Job Number:   {job['job_number']}")
print(f"Customer:     {job['customer']}")
print(f"Part Number:  {job['part_number']}")
print(f"Description:  {job['description']}")
print(f"Qty To Make:  {job['qty_to_make']}")
print(f"Material:     {job['material']}")
print(f"Cut Length:   {job['cut_length']}")


def blank_if_missing(section, key):
    return job[section].get(key, "__________")


print("\n" + "-" * 50)
print("PROGRAMMING")
print("-" * 50)
print(f"Programmer:    {blank_if_missing('programming', 'programmer')}")
print(f"Program Name:  {blank_if_missing('programming', 'program_name')}")
print(f"Revision:      {blank_if_missing('programming', 'revision')}")
print(f"Machine:       {blank_if_missing('programming', 'machine')}")
print(f"Status:        {blank_if_missing('programming', 'status')}")


print("\n" + "-" * 50)
print("SAW CUTTING")
print("-" * 50)
print(f"Employee:      {blank_if_missing('saw_cutting', 'employee')}")
print(f"Qty Cut:       {blank_if_missing('saw_cutting', 'qty_cut')}")
print(f"Cut Length:    {blank_if_missing('saw_cutting', 'cut_length')}")
print(f"Scrap Qty:     {blank_if_missing('saw_cutting', 'scrap_qty')}")
print(f"Status:        {blank_if_missing('saw_cutting', 'status')}")


print("\n" + "-" * 50)
print("CNC MACHINING")
print("-" * 50)
print(f"Operator:      {blank_if_missing('cnc_machining', 'operator')}")
print(f"Machine:       {blank_if_missing('cnc_machining', 'machine')}")
print(f"Qty Complete:  {blank_if_missing('cnc_machining', 'qty_completed')}")
print(f"Qty Rejected:  {blank_if_missing('cnc_machining', 'qty_rejected')}")
print(f"First Article: {blank_if_missing('cnc_machining', 'first_article')}")
print(f"Status:        {blank_if_missing('cnc_machining', 'status')}")


print("\n" + "-" * 50)
print("DEBURR")
print("-" * 50)
print(f"Employee:      {blank_if_missing('deburr', 'employee')}")
print(f"Deburr Needed: {blank_if_missing('deburr', 'deburr_needed')}")
print(f"Qty Deburred:  {blank_if_missing('deburr', 'qty_deburred')}")
print(f"Status:        {blank_if_missing('deburr', 'status')}")


print("\n" + "-" * 50)
print("INSPECTION")
print("-" * 50)
print(f"Inspector:     {blank_if_missing('inspection', 'inspector')}")
print(f"Qty Checked:   {blank_if_missing('inspection', 'qty_checked')}")
print(f"Qty Passed:    {blank_if_missing('inspection', 'qty_passed')}")
print(f"Qty Failed:    {blank_if_missing('inspection', 'qty_failed')}")
print(f"Status:        {blank_if_missing('inspection', 'status')}")


print("\n" + "-" * 50)
print("PACKING")
print("-" * 50)
print(f"Employee:      {blank_if_missing('packing', 'employee')}")
print(f"Qty Packed:    {blank_if_missing('packing', 'qty_packed')}")
print(f"Box Count:     {blank_if_missing('packing', 'box_count')}")
print(f"Status:        {blank_if_missing('packing', 'status')}")


print("\n" + "-" * 50)
print("SHIPPING")
print("-" * 50)
print(f"Employee:      {blank_if_missing('shipping', 'employee')}")
print(f"Ship Date:     {blank_if_missing('shipping', 'ship_date')}")
print(f"Carrier:       {blank_if_missing('shipping', 'carrier')}")
print(f"Tracking:      {blank_if_missing('shipping', 'tracking')}")
print(f"Status:        {blank_if_missing('shipping', 'status')}")
