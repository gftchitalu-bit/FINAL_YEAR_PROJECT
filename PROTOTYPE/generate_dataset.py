"""Creates a SIMULATED dataset of pension complaints (dataset.csv).
No real member data is used. Texts are written from typical Zambian pension
complaint themes (missing contributions, delayed claims, iCARE access, etc.).
Run:  python generate_dataset.py
"""
import csv, random
from pathlib import Path

random.seed(42)
T = {  # category -> urgency -> templates
 'registration': {
  'low': ["I would like to know how to register my new employee with NAPSA.", "Please explain the documents needed to register our company as an employer.", "I want to create an iCARE profile but I do not know where to start."],
  'medium': ["My employer says I was registered but I cannot find my social security number.", "My name was spelt wrongly during registration and it does not match my NRC.", "We submitted our employer registration {n} weeks ago and have not received approval."],
  'high': ["I cannot claim because my registration was never completed even though I have worked for {n} years.", "My NRC details were captured wrongly and my retirement claim has been blocked because of it."]},
 'missing_contributions': {
  'low': ["I noticed one month is missing from my contributions for {yr}.", "My statement skips a month in {yr}. Please check."],
  'medium': ["My payslip shows deductions every month but my NAPSA statement shows nothing for {n} months.", "My employer {emp} deducted my pension but the money does not appear on my record.", "Contributions for {n} months at {emp} are not showing on my account."],
  'high': ["I am retiring this year and {n} years of my contributions are missing from my record.", "I retire next month and discovered that {emp} never paid my contributions."]},
 'employer_arrears': {
  'low': ["Please confirm the exact amount of penalties on our employer account.", "How do we request a payment plan for our arrears?"],
  'medium': ["We paid our contributions but our employer account still shows an outstanding balance of K{amt}.", "We were charged a penalty for {yr} even though our payment was made on time.", "Our bank payment was not matched to our return and now shows as arrears."],
  'high': ["We received a final demand for K{amt} and legal action is threatened but we already paid.", "Our business is flagged for non-compliance and we cannot get a clearance certificate."]},
 'statement_discrepancy': {
  'low': ["My statement shows a different employer name for {yr}.", "The total on my statement does not match my own records. Please explain."],
  'medium': ["My contribution amounts on the statement are lower than what was deducted from my salary.", "My statement shows my salary as K{amt} but I earned more than that.", "Two employers appear on my statement for the same month."],
  'high': ["My statement has errors and I need it corrected urgently because my claim is waiting.", "The errors on my statement are delaying my retirement payment and I need them fixed this week."]},
 'portal_access': {
  'low': ["I forgot my iCARE password and the reset email never arrives.", "The iCARE portal does not show my statement.", "How do I update my phone number on iCARE?"],
  'medium': ["Our payroll upload keeps failing on iCARE with an error message.", "I cannot log in to iCARE after the move from eNAPSA.", "The portal times out when I try to submit our monthly return."],
  'high': ["We cannot file our return before the deadline because iCARE is down and we will be charged a penalty.", "I cannot submit my benefit claim online because the portal keeps rejecting my documents and my retirement date is here."]},
 'delayed_claim': {
  'low': ["Please tell me how long my retirement claim will take.", "I submitted my claim last week and would like an update."],
  'medium': ["I submitted my retirement claim {n} months ago and I have heard nothing.", "My claim has been pending for {n} weeks with no update.", "I keep visiting the office in {town} and they say come back next week."],
  'high': ["I retired {n} months ago and I still have not been paid. I have no income and my family is struggling.", "My claim has been pending for {n} months and I am sick and cannot afford treatment.", "I am an elderly pensioner and my benefits are overdue by {n} months."]},
 'rejected_claim': {
  'low': ["Why was my claim returned and which documents are missing?"],
  'medium': ["My claim was rejected because of missing contributions that my employer failed to pay.", "I was told I do not qualify for a pension but I worked for {n} years.", "My claim was returned twice for different reasons each time."],
  'high': ["My claim was rejected and I am running out of money. I need someone to review it urgently.", "My disability claim was rejected even though I have a medical report and I cannot work."]},
 'payment_issue': {
  'low': ["My pension arrived but the amount looks slightly different from last month.", "Please send me my payment advice for this month."],
  'medium': ["I was paid a lump sum but I expected a monthly pension.", "The amount I received is K{amt} less than the amount quoted to me.", "My pension went to the wrong bank account."],
  'high': ["My pension has not been paid for {n} months and I depend on it for food and rent.", "My monthly pension did not arrive and I have no other income."]},
 'life_certificate': {
  'low': ["How do I renew my life certificate?", "Where can I submit my life certificate if I live in {town}?"],
  'medium': ["I submitted my life certificate but the system still shows it as expired.", "I live outside the country and cannot easily submit my life certificate."],
  'high': ["My pension was stopped because of my life certificate even though I submitted it on time.", "My mother's pension has stopped and she is bedridden and cannot travel to renew her certificate."]},
 'survivor_funeral': {
  'low': ["Please explain what documents are needed to claim the funeral grant.", "How is the survivors benefit shared between the children and the spouse?"],
  'medium': ["My late husband's survivors benefit claim has not been processed yet.", "There is a dispute in the family over the survivors benefit.", "We submitted the funeral grant forms {n} weeks ago."],
  'high': ["My husband died {n} months ago and we have not received the funeral grant or survivors benefit. We have children in school.", "We buried our father last week and need the funeral grant urgently.", "I am a widow with young children and I have received nothing since my husband passed away."]},
 'service_conduct': {
  'low': ["The officer I met was very helpful.", "I would like to compliment the customer service team in {town}."],
  'medium': ["The staff at the {town} office were rude and unhelpful.", "I was sent from one desk to another for three days without help.", "Nobody answers the call centre number."],
  'high': ["An officer asked me for money to process my claim.", "I was told I must pay to have my claim speeded up."]},
}
ROLE = {'employer_arrears': 'employer', 'survivor_funeral': 'beneficiary'}
EMP = ['Kabwe Hardware Ltd', 'Copperline Mining', 'Zambezi Foods', 'Mulonga Transport', 'the council', 'a private school', 'Chisamba Farms Ltd']
TOWN = ['Kabwe', 'Lusaka', 'Ndola', 'Kitwe', 'Livingstone', 'Chipata', 'Mansa']
OPEN = ['', '', 'Good day. ', 'To whom it may concern, ', 'Hello, ']
CLOSE = {'negative': ['This is unacceptable.', 'I am very disappointed.', 'Please help me, I am tired of waiting.', 'This has caused me a lot of stress.'],
         'neutral': ['Please advise.', 'Kindly assist.', 'Thank you in advance.', ''],
         'positive': ['Thank you for your help.', 'Zikomo.', 'Natotela.', 'I appreciate the support.']}
SENT_P = {'low': (0.25, 0.60, 0.15), 'medium': (0.60, 0.35, 0.05), 'high': (0.90, 0.08, 0.02)}

def build(n=2400):
    rows = []
    cats = list(T)
    for i in range(n):
        cat = cats[i % len(cats)]
        urg = random.choices(['low', 'medium', 'high'], weights=[3, 5, 3])[0]
        if urg not in T[cat]: urg = 'medium'
        text = random.choice(T[cat][urg]).format(n=random.randint(2, 14), yr=random.randint(2019, 2026), amt=f'{random.randint(2, 90) * 100:,}', emp=random.choice(EMP), town=random.choice(TOWN))
        p = SENT_P[urg]
        if cat == 'service_conduct' and urg == 'low': p = (0.05, 0.15, 0.80)
        sent = random.choices(['negative', 'neutral', 'positive'], weights=p)[0]
        text = random.choice(OPEN) + text + ' ' + random.choice(CLOSE[sent])
        if random.random() < 0.15: text += f' My NRC is {random.randint(100000, 999999)}/{random.randint(10, 99)}/1.'
        if random.random() < 0.10: text = text.lower()
        lab = [cat, urg, sent]
        if random.random() < 0.02: lab[0] = random.choice(cats)      # label noise
        if random.random() < 0.03: lab[1] = random.choice(['low', 'medium', 'high'])
        if random.random() < 0.03: lab[2] = random.choice(['negative', 'neutral', 'positive'])
        inst = random.choices(['NAPSA', 'PSPF', 'LASF'], weights=[70, 20, 10])[0]
        rows.append([text.strip(), lab[0], lab[1], lab[2], ROLE.get(cat, 'employee'), inst])
    random.shuffle(rows)
    return rows

if __name__ == '__main__':
    out = Path(__file__).parent / 'dataset.csv'
    with open(out, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f); w.writerow(['text', 'category', 'urgency', 'sentiment', 'role', 'institution']); w.writerows(build())
    print('Wrote', out)
