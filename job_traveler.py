# job_traveler.py
def get_int(prompt):
    while True:
        raw = input(prompt).strip()
        try:
            return int(raw)
        except ValueError:
            print("Invalid number. Enter a whole number.")


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
    "shipping": {},
   
    
}

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

