# job_traveler.py
import json
import os


BLANK = "__________"
JOBS_FOLDER = "jobs"

SECTIONS = [
    "programming",
    "saw_cutting",
    "cnc_machining",
    "deburr",
    "inspection",
    "packing",
    "shipping",
]


def get_int(prompt):
    while True:
        raw = input(prompt).strip()
        try:
            return int(raw)
        except ValueError:
            print("Invalid number. Enter a whole number.")


def save_job(job):
    os.makedirs(JOBS_FOLDER, exist_ok=True)

    filename = os.path.join(JOBS_FOLDER, f"{job['job_number']}.json")

    with open(filename, "w") as file:
        json.dump(job, file, indent=4)

    print(f"\nSaved job traveler to {filename}")


def load_job(job_number):
    filename = os.path.join(JOBS_FOLDER, f"{job_number}.json")

    if not os.path.exists(filename):
        print(f"\nNo job traveler found for job number {job_number}.")
        return None

    with open(filename, "r") as file:
        job = json.load(file)

    for section in SECTIONS:
        job.setdefault(section, {})

    return job


def create_new_job():
    print("\nCreate New Job Traveler")
    print("-" * 30)

    job = {
        "job_number": input("Job Number: ").strip(),
        "customer": input("Customer: ").strip(),
        "part_number": input("Part Number: ").strip(),
        "description": input("Description: ").strip(),
        "qty_to_make": get_int("Qty To Make: "),
        "material": input("Material: ").strip(),
        "cut_length": input("Cut Length: ").strip(),
        "programming": {},
        "saw_cutting": {},
        "cnc_machining": {},
        "deburr": {},
        "inspection": {},
        "packing": {},
        "shipping": {},
    }

    save_job(job)
    return job


def blank_if_missing(job, section, key):
    value = job.get(section, {}).get(key)

    if value == "" or value is None:
        return BLANK

    return value


def job_field(job, key):
    value = job.get(key)

    if value == "" or value is None:
        return BLANK

    return value


def print_traveler(job):
    print("\n" + "=" * 60)
    print("JOB TRAVELER")
    print("=" * 60)

    print(f"Job Number:   {job_field(job, 'job_number')}")
    print(f"Customer:     {job_field(job, 'customer')}")
    print(f"Part Number:  {job_field(job, 'part_number')}")
    print(f"Description:  {job_field(job, 'description')}")
    print(f"Qty To Make:  {job_field(job, 'qty_to_make')}")
    print(f"Material:     {job_field(job, 'material')}")
    print(f"Cut Length:   {job_field(job, 'cut_length')}")

    print("\n" + "-" * 60)
    print("PROGRAMMING")
    print("-" * 60)
    print(f"Programmer:    {blank_if_missing(job, 'programming', 'programmer')}")
    print(f"Program Name:  {blank_if_missing(job, 'programming', 'program_name')}")
    print(f"Revision:      {blank_if_missing(job, 'programming', 'revision')}")
    print(f"Machine:       {blank_if_missing(job, 'programming', 'machine')}")
    print(f"Status:        {blank_if_missing(job, 'programming', 'status')}")
    print(f"Notes:         {blank_if_missing(job, 'programming', 'notes')}")

    print("\n" + "-" * 60)
    print("SAW CUTTING")
    print("-" * 60)
    print(f"Employee:      {blank_if_missing(job, 'saw_cutting', 'employee')}")
    print(f"Qty Cut:       {blank_if_missing(job, 'saw_cutting', 'qty_cut')}")
    print(f"Cut Length:    {blank_if_missing(job, 'saw_cutting', 'cut_length')}")
    print(f"Scrap Qty:     {blank_if_missing(job, 'saw_cutting', 'scrap_qty')}")
    print(f"Status:        {blank_if_missing(job, 'saw_cutting', 'status')}")
    print(f"Notes:         {blank_if_missing(job, 'saw_cutting', 'notes')}")

    print("\n" + "-" * 60)
    print("CNC MACHINING")
    print("-" * 60)
    print(f"Operator:      {blank_if_missing(job, 'cnc_machining', 'operator')}")
    print(f"Machine:       {blank_if_missing(job, 'cnc_machining', 'machine')}")
    print(f"Qty Complete:  {blank_if_missing(job, 'cnc_machining', 'qty_completed')}")
    print(f"Qty Rejected:  {blank_if_missing(job, 'cnc_machining', 'qty_rejected')}")
    print(f"First Article: {blank_if_missing(job, 'cnc_machining', 'first_article')}")
    print(f"Status:        {blank_if_missing(job, 'cnc_machining', 'status')}")
    print(f"Notes:         {blank_if_missing(job, 'cnc_machining', 'notes')}")

    print("\n" + "-" * 60)
    print("DEBURR")
    print("-" * 60)
    print(f"Employee:      {blank_if_missing(job, 'deburr', 'employee')}")
    print(f"Deburr Needed: {blank_if_missing(job, 'deburr', 'deburr_needed')}")
    print(f"Qty Deburred:  {blank_if_missing(job, 'deburr', 'qty_deburred')}")
    print(f"Status:        {blank_if_missing(job, 'deburr', 'status')}")
    print(f"Notes:         {blank_if_missing(job, 'deburr', 'notes')}")

    print("\n" + "-" * 60)
    print("INSPECTION")
    print("-" * 60)
    print(f"Inspector:     {blank_if_missing(job, 'inspection', 'inspector')}")
    print(f"Qty Checked:   {blank_if_missing(job, 'inspection', 'qty_checked')}")
    print(f"Qty Passed:    {blank_if_missing(job, 'inspection', 'qty_passed')}")
    print(f"Qty Failed:    {blank_if_missing(job, 'inspection', 'qty_failed')}")
    print(f"Status:        {blank_if_missing(job, 'inspection', 'status')}")
    print(f"Notes:         {blank_if_missing(job, 'inspection', 'notes')}")

    print("\n" + "-" * 60)
    print("PACKING")
    print("-" * 60)
    print(f"Employee:      {blank_if_missing(job, 'packing', 'employee')}")
    print(f"Qty Packed:    {blank_if_missing(job, 'packing', 'qty_packed')}")
    print(f"Box Count:     {blank_if_missing(job, 'packing', 'box_count')}")
    print(f"Status:        {blank_if_missing(job, 'packing', 'status')}")
    print(f"Notes:         {blank_if_missing(job, 'packing', 'notes')}")

    print("\n" + "-" * 60)
    print("SHIPPING")
    print("-" * 60)
    print(f"Employee:      {blank_if_missing(job, 'shipping', 'employee')}")
    print(f"Ship Date:     {blank_if_missing(job, 'shipping', 'ship_date')}")
    print(f"Carrier:       {blank_if_missing(job, 'shipping', 'carrier')}")
    print(f"Tracking:      {blank_if_missing(job, 'shipping', 'tracking')}")
    print(f"Status:        {blank_if_missing(job, 'shipping', 'status')}")
    print(f"Notes:         {blank_if_missing(job, 'shipping', 'notes')}")


def update_programming(job):
    print("\nUpdate Programming")
    print("-" * 30)

    job["programming"] = {
        "programmer": input("Programmer: ").strip(),
        "program_name": input("Program Name: ").strip(),
        "revision": input("Revision: ").strip(),
        "machine": input("Machine: ").strip(),
        "status": input("Status: ").strip(),
        "notes": input("Notes: ").strip(),
    }

    save_job(job)


def update_saw_cutting(job):
    print("\nUpdate Saw Cutting")
    print("-" * 30)

    job["saw_cutting"] = {
        "employee": input("Employee: ").strip(),
        "qty_cut": get_int("Qty Cut: "),
        "cut_length": input("Cut Length: ").strip(),
        "scrap_qty": get_int("Scrap Qty: "),
        "status": input("Status: ").strip(),
        "notes": input("Notes: ").strip(),
    }

    save_job(job)


def update_cnc_machining(job):
    print("\nUpdate CNC Machining")
    print("-" * 30)

    job["cnc_machining"] = {
        "operator": input("Operator: ").strip(),
        "machine": input("Machine: ").strip(),
        "qty_completed": get_int("Qty Completed: "),
        "qty_rejected": get_int("Qty Rejected: "),
        "first_article": input("First Article: ").strip(),
        "status": input("Status: ").strip(),
        "notes": input("Notes: ").strip(),
    }

    save_job(job)


def update_deburr(job):
    print("\nUpdate Deburr")
    print("-" * 30)

    job["deburr"] = {
        "employee": input("Employee: ").strip(),
        "deburr_needed": input("Deburr Needed: ").strip(),
        "qty_deburred": get_int("Qty Deburred: "),
        "status": input("Status: ").strip(),
        "notes": input("Notes: ").strip(),
    }

    save_job(job)


def update_inspection(job):
    print("\nUpdate Inspection")
    print("-" * 30)

    job["inspection"] = {
        "inspector": input("Inspector: ").strip(),
        "qty_checked": get_int("Qty Checked: "),
        "qty_passed": get_int("Qty Passed: "),
        "qty_failed": get_int("Qty Failed: "),
        "status": input("Status: ").strip(),
        "notes": input("Notes: ").strip(),
    }

    save_job(job)


def update_packing(job):
    print("\nUpdate Packing")
    print("-" * 30)

    job["packing"] = {
        "employee": input("Employee: ").strip(),
        "qty_packed": get_int("Qty Packed: "),
        "box_count": get_int("Box Count: "),
        "status": input("Status: ").strip(),
        "notes": input("Notes: ").strip(),
    }

    save_job(job)


def update_shipping(job):
    print("\nUpdate Shipping")
    print("-" * 30)

    job["shipping"] = {
        "employee": input("Employee: ").strip(),
        "ship_date": input("Ship Date: ").strip(),
        "carrier": input("Carrier: ").strip(),
        "tracking": input("Tracking: ").strip(),
        "status": input("Status: ").strip(),
        "notes": input("Notes: ").strip(),
    }

    save_job(job)


def show_updated_traveler_and_choose_next(job):
    print_traveler(job)

    while True:
        print("\nWhat next?")
        print("1. Update another section")
        print("0. Exit to Main Menu")

        choice = input("Choose an option: ").strip()

        if choice == "1":
            return True
        if choice == "0":
            return False

        print("Invalid choice. Please try again.")


def job_menu(job):
    while True:
        print("\nJob Menu")
        print("-" * 30)
        print("1. Update Programming")
        print("2. Update Saw Cutting")
        print("3. Update CNC Machining")
        print("4. Update Deburr")
        print("5. Update Inspection")
        print("6. Update Packing")
        print("7. Update Shipping")
        print("8. Print Traveler")
        print("9. Save Job")
        print("0. Exit to Main Menu")

        choice = input("Choose an option: ").strip()

        if choice == "1":
            update_programming(job)
            if not show_updated_traveler_and_choose_next(job):
                return
        elif choice == "2":
            update_saw_cutting(job)
            if not show_updated_traveler_and_choose_next(job):
                return
        elif choice == "3":
            update_cnc_machining(job)
            if not show_updated_traveler_and_choose_next(job):
                return
        elif choice == "4":
            update_deburr(job)
            if not show_updated_traveler_and_choose_next(job):
                return
        elif choice == "5":
            update_inspection(job)
            if not show_updated_traveler_and_choose_next(job):
                return
        elif choice == "6":
            update_packing(job)
            if not show_updated_traveler_and_choose_next(job):
                return
        elif choice == "7":
            update_shipping(job)
            if not show_updated_traveler_and_choose_next(job):
                return
        elif choice == "8":
            print_traveler(job)
        elif choice == "9":
            save_job(job)
        elif choice == "0":
            return
        else:
            print("Invalid choice. Please try again.")


def main():
    while True:
        print("\nMain Menu")
        print("-" * 30)
        print("1. Create New Job Traveler")
        print("2. Open Existing Job Traveler")
        print("0. Exit")

        choice = input("Choose an option: ").strip()

        if choice == "1":
            job = create_new_job()
            job_menu(job)
        elif choice == "2":
            job_number = input("Job Number: ").strip()
            job = load_job(job_number)

            if job is not None:
                job_menu(job)
        elif choice == "0":
            print("Goodbye.")
            return
        else:
            print("Invalid choice. Please try again.")


if __name__ == "__main__":
    main()
