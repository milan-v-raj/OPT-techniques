"""Build an editable course deck and matching PDF handout from report values.
Run: python build_deck.py (requires python-pptx and reportlab).
The PDF is drawn from the same layout, not converted from PowerPoint.
"""
from pathlib import Path
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

OUT = Path(__file__).resolve().parent
FONTDIR = Path('/usr/share/fonts/truetype/dejavu')
for name, filename in [('Regular', 'DejaVuSans.ttf'), ('Bold', 'DejaVuSans-Bold.ttf')]:
    pdfmetrics.registerFont(TTFont(name, str(FONTDIR / filename)))
W, H = 960, 540
INK, MUTED, ACCENT, LIGHT, LINE = '20354A', '536171', '18756D', 'EFF5F4', 'D7DFE5'
prs = Presentation()
prs.slide_width, prs.slide_height = Inches(W/72), Inches(H/72)
prs.core_properties.title = 'Grid-Aware Smart EV Charging'
prs.core_properties.author = 'Group 1 — Tarun Thomas Tom, Milan V Raj, Nirmal Thomas Mathew'
prs.core_properties.subject = 'Optimization Techniques course project'
pdf = canvas.Canvas(str(OUT/'Grid-Aware Smart EV Charging - Revised.pdf'), pagesize=(W,H))
pdf.setTitle('Grid-Aware Smart EV Charging — Revised')
slide = None
notes = []
boxes = []

def color(s): return RGBColor.from_string(s)
def rect(x,y,w,h,fill):
    sh=slide.shapes.add_shape(1,Pt(x),Pt(y),Pt(w),Pt(h))
    sh.fill.solid(); sh.fill.fore_color.rgb=color(fill); sh.line.fill.background()
    pdf.setFillColor('#'+fill); pdf.rect(x,H-y-h,w,h,fill=1,stroke=0)

def line(x,y,w,c=LINE): rect(x,y,w,0.7,c)

def wrap(text,size,width,bold=False):
    font='Bold' if bold else 'Regular'
    result=[]
    for para in text.split('\n'):
        if not para: result.append(''); continue
        current=''
        for word in para.split():
            test=(current+' '+word).strip()
            if pdfmetrics.stringWidth(test,font,size)>width and current:
                result.append(current); current=word
            else: current=test
        result.append(current)
    return result

def text(x,y,w,value,size=18,bold=False,c=INK,leading=None):
    leading=leading or size*1.32
    lines=wrap(value,size,w,bold)
    h=len(lines)*leading+5
    assert x+w<=W+1 and y+h<=H+1, (value,y,h)
    tb=slide.shapes.add_textbox(Pt(x),Pt(y),Pt(w+2),Pt(h))
    tf=tb.text_frame; tf.clear(); tf.word_wrap=False
    tf.margin_left=tf.margin_right=tf.margin_top=tf.margin_bottom=0
    for i,st in enumerate(lines):
        p=tf.paragraphs[0] if i==0 else tf.add_paragraph()
        p.text=st; p.font.name='DejaVu Sans'; p.font.size=Pt(size); p.font.bold=bold; p.font.color.rgb=color(c)
        p.space_before=Pt(0); p.space_after=Pt(0); p.line_spacing=Pt(leading)
        pdf.setFont('Bold' if bold else 'Regular',size); pdf.setFillColor('#'+c)
        pdf.drawString(x,H-y-size-i*leading,st)
    boxes.append((len(prs.slides),x,y,w,h,value))
    return h

def start(title,section,source,number=None):
    global slide
    if slide is not None: pdf.showPage()
    slide=prs.slides.add_slide(prs.slide_layouts[6])
    rect(0,0,W,H,'FFFFFF')
    text(40,23,850,section.upper(),10,True,ACCENT)
    text(40,51,880,title,27,True)
    line(40,101,880)
    line(40,502,880)
    text(40,511,820,source,8.5,c=MUTED)
    text(889,510,40,str(number or len(prs.slides)),10,c=MUTED)

def note(value):
    slide.notes_slide.notes_text_frame.text=value
    notes.append((len(prs.slides),value))

def bullet(x,y,w,heading,body):
    text(x,y,w,heading,18,True)
    return text(x,y+30,w,body,16,c=MUTED)

def table(x,y,width,headers,rows,weights,fs=15,row_h=43,highlight=()):
    cols=[width*v/sum(weights) for v in weights]
    rect(x,y,width,row_h,INK)
    xx=x
    for h,cw in zip(headers,cols): text(xx+10,y+11,cw-20,h,fs-1,True,'FFFFFF'); xx+=cw
    yy=y+row_h
    for idx,row in enumerate(rows):
        if idx in highlight: rect(x,yy,width,row_h,LIGHT)
        elif idx%2==0: rect(x,yy,width,row_h,'F5F7F9')
        xx=x
        for val,cw in zip(row,cols):
            text(xx+10,yy+11,cw-20,str(val),fs,idx in highlight,ACCENT if idx in highlight else INK)
            xx+=cw
        line(x,yy+row_h,width)
        yy+=row_h
    return yy

# 1
start('Grid-Aware Smart EV Charging','Optimization Techniques · Group 1','Course project • Simulation study using Caltech ACN charging sessions')
text(40,139,795,'Scheduling charging currents with convex optimization',31,True,leading=42)
text(40,249,760,'How can a workplace charge its EVs without creating an expensive demand peak?',23,c=MUTED)
line(40,353,130,ACCENT)
text(40,379,860,'Tarun Thomas Tom  ·  Milan V Raj  ·  Nirmal Thomas Mathew',18)
text(40,421,850,'Real charging data  /  CVXPY + Clarabel  /  Five-minute control',15,c=MUTED)
note('Opening: Our project looks at a workplace garage where many EVs arrive together. We choose charging currents rather than letting every car charge immediately. The goal is to lower the bill without overloading the network or leaving too much charging demand unmet. This is a simulation study using real session data, not a live deployment. Suggested duration for slides 1–12: about 10–12 minutes. Speaker split can be 1–5, 6–9, and 10–12.')

# 2
start('Why charging on arrival is a problem','Problem and motivation','Report §§I–II; Table III • Dollar values are rounded')
bullet(40,129,400,'Many arrivals, one shared connection','Most drivers arrive between 7 and 10 a.m. Charging immediately concentrates the load.')
bullet(40,249,400,'The bill depends on the peak','The tariff adds $15.51 per kW of the month’s highest 15-minute average demand.')
bullet(40,369,400,'Parking time gives us flexibility','Vehicles often stay longer than the time needed to charge. We can spread the demand.')
text(493,129,420,'Uncontrolled September bill',19,True)
for y,label,value in [(191,'Energy charge',1874),(283,'Demand charge',2972)]:
    text(493,y,410,label,16,c=MUTED)
    rect(493,y+31,value/2972*325,27,ACCENT if value==2972 else '94A5B5')
    text(830,y+31,85,f'${value:,}',15,True)
text(493,394,410,'The demand charge is larger than the energy charge.',19,True)
note('Explain the distinction between energy and demand: energy is total kWh, while demand is the highest billed average kW. The uncontrolled case has about $1,874 of energy charges and $2,972 of demand charges. Those rounded components sum to $4,846 while the table reports a rounded total of $4,845. Do not interpret the uncontrolled schedule as feasible: it exceeds a network limit by up to 2.06 times. The useful flexibility is the difference between parking time and required charging time.')

# 3
start('What we build on','Related work','Report §III and references [1]–[5] • Full references in backup slide 15')
bullet(40,128,410,'Adaptive Charging Networks','Lee et al. (2021): convex MPC with three-phase network limits. This is our starting framework.')
bullet(40,250,410,'Valley-filling charging','Gan, Topcu and Low (2013): flatten aggregate demand with a convex load objective.')
bullet(40,372,410,'Data and simulation tools','ACN-Data supplies sessions; ACN-Sim evaluates charging schedules.')
rect(493,128,427,335,LIGHT)
text(516,149,378,'What this project studies',20,True)
text(516,199,378,'1. Monthly peak-demand billing\n\n2. Uncertain driver requirements\n\n3. Solar generation and forecast errors',18)
note('We are not claiming to invent convex EV charging or MPC. Our contribution is integrating and evaluating practical extensions within an existing framework. The three main extensions are monthly billing, historical scenarios for driver uncertainty, and behind-the-meter PV. Bertsimas–Sim robust optimization is also used in an exploratory daily appendix experiment, separate from the main S2–S4 results.')

# 4
start('System and experimental setup','System model','Report §II, §IV and §VI • Phase assignment and PV weather are synthetic')
table(40,126,490,['Item','Setting'],[
('Charging facility','Caltech ACN garage'),('Network','54 chargers; 3 phases; 150 kVA'),('Dataset','1,772 sessions; 19 weekdays'),('Study period','September 2018'),('Control / horizon','5 minutes / up to 12 hours')],[1,1.8],fs=14,row_h=50)
text(569,131,350,'Three main experiments',20,True)
bullet(569,179,350,'S2 · Monthly billing','Known needs and departures of connected EVs.')
bullet(569,276,350,'S3 · Predicted drivers','Requirements estimated from the previous four weeks.')
bullet(569,373,350,'S4 · Add solar','50 kW PV; forecast output; bill on net grid import.')
note('The transformer rating is 150 kVA, not 150 kW. The controller sees a real EV only when it plugs in. Perfect information in the online cases means known requirements of connected EVs, not knowledge of every future arrival. OPT is an offline benchmark with future information. The model uses continuous current control, approximately 0–32 A. The simpler daily S1 setup is in the report appendix; this presentation focuses on S2–S4.')

# 5
start('Decision variables and constraints','Problem formulation','Report §IV-A–C • Δ = 5/60 hours when computing kWh')
text(40,125,880,'Choose R(i,t): the charging current of EV i in interval t.',20,True)
table(40,178,880,['Constraint','Mathematical form','Meaning'],[
('Charging limit','0 ≤ R(i,t) ≤ r̄(i,t)','Zero outside the plug-in window'),('Energy requirement','Σₜ E(i,t) + u(i) = e(i)','u(i) ≥ 0 is unmet energy'),('Network limits','‖B(m) R(:,t)‖₂ ≤ c(m)','Phase and transformer limits')],[1.1,1.45,1.8],fs=16,row_h=52)
text(40,408,880,'Power: P(t) = Σᵢ V(i) R(i,t) / 1000   |   Energy: E(i,t) = V(i) R(i,t) Δ / 1000',16)
text(40,451,880,'Unmet energy is allowed but penalized, so tight charging windows need not make the problem infeasible.',15,c=MUTED)
note('R is the decision variable in amperes. The availability bound becomes zero before arrival and after departure. The energy equation includes a nonnegative slack u, which is penalized rather than forced to zero. The report found hard energy requirements could become infeasible because recorded requests do not always fit rounded five-minute windows. B(m) is a two-row matrix with A(m,i)cos(phi_i) and A(m,i)sin(phi_i), so its Euclidean norm is the magnitude of the current phasor sum. This is a second-order cone constraint. Voltage is treated as a model parameter. This is not a full AC power-flow model.')

# 6
start('What does the optimizer minimize?','Objective and convexity','Report §IV-D; Table I • Full monthly-demand expression in backup slide 13')
text(40,127,880,'Electricity cost + charging shortfall + optional grid-quality penalties',22,True)
bullet(40,188,414,'BILL-LP','Time-of-use energy cost, demand charge, peak-exceedance penalty and unmet energy.')
bullet(40,313,414,'COMBINED-QP','The same terms, plus quadratic penalties for load flattening and phase imbalance.')
rect(498,187,422,242,LIGHT)
text(519,206,379,'Why the problem is convex',20,True)
text(519,250,379,'Linear charging constraints\nConvex max and hinge costs\nConvex quadratic penalties\nSecond-order cone network limits',17,leading=32)
text(40,453,880,'Despite its name, BILL-LP is an SOCP once the network constraints are included.',17,True,ACCENT)
note('The default unmet-energy penalty is $100/kWh. BILL-LP refers to the objective, not the class of the full problem. With the network cones it is an SOCP. COMBINED-QP has a convex quadratic objective and SOC constraints; it can also be expressed as an SOCP using an epigraph reformulation. With nonnegative weights the formulation is convex. A global optimum of the current planning problem is not a guarantee of a globally optimal realized monthly bill, because the online controller lacks future information.')

# 7
start('How we solve it in practice','MPC and software','Report §IV-E and §V-A • CVXPY models the problem; Clarabel solves it')
steps=[('Observe','Connected EVs, energy delivered, paid peak'),('Optimize','Plan charging over a horizon of up to 12 h'),('Apply','Use only the first 5-minute decision'),('Repeat','Update information and solve again')]
for k,(head,body) in enumerate(steps):
    yy=127+k*79
    text(40,yy,35,str(k+1),22,True,ACCENT)
    text(83,yy,380,head,19,True)
    text(83,yy+29,373,body,15,c=MUTED)
line(488,128,1)
text(524,128,390,'Implementation stack',20,True)
for yy,head,body in [(179,'Python + CVXPY','Variables, objective and constraints'),(254,'Clarabel','Primary conic solver; SCS as fallback'),(329,'ACN-Sim + ACN-Data','Simulation and historical sessions')]:
    text(524,yy,390,head,18,True); text(524,yy+29,390,body,15,c=MUTED)
text(524,422,390,'MPC is the control strategy, not the solver.',17,True,ACCENT)
note('At each control step we observe the state, update predictions, rebuild the convex problem and solve it. We implement just the first action, then repeat after five minutes. CVXPY translates the mathematical model into the solver representation; Clarabel computes the solution. SCS is a configured fallback, but the report says none of the monthly MPC runs needed it. ACN-Sim is the evaluation environment, not the optimization solver. The current repository snapshot contains figures and this deck, not the experiment code; implementation details here are reported in the project PDF.')

# 8
start('Monthly billing: lower peak, lower bill','S2 results · Known driver requirements','Report Table III • Both RR and MPC respect modeled network limits')
table(40,128,880,['Controller','Bill','15-min peak','Delivery'],[
('Round Robin (RR)','$4,088','142.5 kW','99.8%'),('MPC-BILL-LP','$3,031','70.3 kW','98.9%'),('MPC-COMBINED-QP','$3,518','77.6 kW','96.5%'),('Offline OPT-BILL-LP','$3,150','71.2 kW','99.6%')],[2.25,1,1.1,1],fs=17,row_h=52,highlight=(1,))
text(40,415,880,'Compared with RR: $1,057 lower bill (26%) and 51% lower billed peak.',20,True,ACCENT)
text(40,454,880,'Trade-off: delivery falls by 0.9 percentage points. Read savings together with service.',16,c=MUTED)
note('Round Robin is the cheapest reported heuristic: $4,088, compared with $4,096 for LLF and $4,116 for EDF. The relative bill reduction is 25.9% and the billed-peak reduction is 50.7%. Do not say EDF is the best heuristic. The energy charge for MPC-BILL-LP is actually slightly higher than for RR; the reduction in demand charge drives the total savings. The offline schedule delivers more energy and optimizes a penalized objective, so MPC having a lower realized bill does not prove it beats a global optimum. The report’s displayed 19.9 versus 20.6 cents/kWh is about a 3.4% difference, not a rigorous 3% optimality bound.')

# 9
start('Predictions alone can leave EVs undercharged','S3 results · Uncertain driver requirements','Report §IV-F(a), §V-C; original deck S3 table • SAA weight selected in-sample')
text(40,125,880,'MPC-COMBINED-QP, with different models of driver behavior',19,True)
table(40,170,880,['Information model','Bill','Delivery'],[
('Median prediction','$2,782','76.4%'),('Conservative prediction','$3,470','94.3%'),('Historical scenarios (SAA-30)','$3,178','96.3%')],[2.8,1,1],fs=18,row_h=55,highlight=(2,))
text(40,418,880,'SAA penalizes expected unmet energy across up to 20 historical scenarios per EV.',18,True)
text(40,457,880,'Delivery rises by 19.9 percentage points over Median. A low bill alone is not success.',16,c=MUTED)
note('Median uses a typical stay and energy request estimated from the previous four weeks. Conservative uses the 25th percentile stay and 75th percentile energy. SAA replaces a single target with weighted historical outcomes, conditioned on the vehicle still being present and not finished. The penalty is $30/kWh for SAA-30. The expected-shortfall term is piecewise linear and convex; the complete charging problem retains its SOC constraints. It is not a chance constraint and does not guarantee a service probability. The $2,782 median bill and 94.3% conservative delivery are transcribed from the original presentation’s S3 results. SAA-30 was tuned on the same month, so generalization needs further testing.')

# 10
start('Solar helps, but forecast errors can raise the peak','S4 results · 50 kW PV','Report §V-D and Table VI • Synthetic PV weather; zero export revenue')
text(40,128,405,'Why the peak can keep rising',20,True)
text(40,179,405,'1. Schedule close to the peak already paid for.\n\n2. Solar output falls below forecast.\n\n3. Grid import sets a higher peak for the rest of the month.',18)
text(40,414,405,'Flattening and scenario-based scheduling reduce this effect in the tests.',17,True,ACCENT)
text(488,128,432,'Selected online configurations',20,True)
table(488,174,432,['Controller / info','Bill','Delivery'],[
('Bill / Perfect','$2,514','98.6%'),('Combined / SAA+F','$1,904','97.8%')],[2.2,1,1.1],fs=13,row_h=56,highlight=(1,))
text(488,375,432,'Billed import peak: 97.2 → 60.2 kW',18,True)
text(488,418,432,'SAA+F uses scenarios and a corrected future-arrival forecast.',15,c=MUTED)
note('PV is billed through nonnegative net import, with no export compensation. The bill-focused controller has little incentive to stay below a peak already paid for; solar forecast misses can ratchet that peak upward. The comparison changes the objective and information treatment, so do not attribute the entire difference to one term. Combined-QP with SAA-30 and corrected arrival forecasts costs $1,904 with a 60.2 kW peak and 97.8% delivery. It is the lowest-bill configuration among the listed S4 online results delivering at least 95%, not the absolute cheapest: a median-prediction controller costs $1,629 but delivers only 75.8%. OPT-BILL-LP with known future PV costs $1,675 and delivers 99.6%. +F denotes virtual future arrivals, with windows and energy corrected at the planning-horizon boundary.')

# 11
start('Can it finish before the next control step?','Computation','Report Table II • Wall time includes model build, CVXPY compilation and solve')
text(40,126,880,'The controller has 300 seconds between charging updates.',22,True)
table(40,184,880,['Online configuration','Median','99th percentile','Maximum'],[
('S2 · BILL-LP','0.09 s','2.29 s','3.8 s'),('S3 · SAA-30, Combined','0.15 s','8.27 s','10.3 s'),('S4 · SAA-30, Combined+F','1.31 s','12.79 s','21.4 s')],[2.5,1,1.3,1],fs=17,row_h=55)
text(40,430,880,'All tested online solves fit within the five-minute interval.',21,True,ACCENT)
text(40,466,880,'The offline joint-month benchmark takes about 10–17 minutes and is not an online controller.',14,c=MUTED)
note('The timing statement applies to the reported tests, not every possible deployment. Deterministic calls are fast at the median, but scenario variables and future-arrival forecasts add cost. The maximum listed online call is 21.4 seconds, about 7.1% of a 300-second interval. Clarabel and CVXPY timing includes model construction and compilation. No monthly MPC run produced an infeasible schedule or needed the fallback solver. The offline 19-day solves took 595 and 1,031 seconds; do not include them in the claim that every solve is below five minutes.')

# 12
start('What we learned—and what remains to test','Conclusion','Report §VI • Results are simulation findings, not a live-deployment guarantee')
text(40,129,420,'Useful outcomes',21,True)
bullet(40,181,420,'Cost and peak reduction','About 26% lower bill and 51% lower billed peak than the cheapest heuristic in S2.')
bullet(40,300,420,'Better delivery with uncertainty','Historical scenarios recover much of the delivery lost with median predictions.')
text(510,129,410,'Limitations',21,True)
text(510,181,410,'One month at one site\nSynthetic phase assignment and PV weather\nContinuous, not discrete, charging currents\nScenario weight tuned on the test month\nNo PV-aware heuristic baseline',17,leading=35)
line(40,420,880)
text(40,444,880,'Next: test other months, tune weights out of sample and handle real charger pilot levels.',18,True,ACCENT)
note('Close with the practical conclusion: the model produces schedules that trade electricity cost, grid feasibility and charging service. Cost alone is not an adequate evaluation metric. We have evidence of useful results in simulation, not a broad deployment guarantee. The mathematical framework is convex under the modeling assumptions, but hardware uses discrete pilots and uncertainty can affect realized performance. Future experiments should use other months and sites, measured PV, and PV-aware heuristic baselines. Pause for questions; slides 13–15 are backup material.')

# 13
start('Monthly demand and scenario shortfalls','Backup A · Mathematical details','Report §IV-D and §IV-F • Mathematical symbols use indexed text for editability')
text(40,125,880,'15-minute billed demand and the planning floor',19,True)
text(40,164,880,'P̄(t) = [P(t) + P(t−1) + P(t−2)] / 3',21)
text(40,202,880,'F = max{P*, F_mtd}       P* = 54.93 kW',21)
text(40,244,880,'Demand epigraph: q ≥ P̄(t),  q ≥ F       Add d · q to the objective.',18)
line(40,292,880)
text(40,314,880,'Scenario-based expected unmet energy',19,True)
text(40,355,880,'z(i,s) ≥ k(i,s) − C(i,d(i,s)),       z(i,s) ≥ 0',21)
text(40,395,880,'Penalty: w Σᵢ Σₛ π(i,s) z(i,s)       with w = 30 $/kWh',20)
text(40,453,880,'With PV, billing uses net import S(t) ≥ P(t) − PV_forecast(t),  S(t) ≥ 0.',16,c=MUTED)
note('P* is a historical planning target, not a hard physical limit. F_mtd is the month-to-date billed peak. The epigraph variable q represents the maximum over planned 15-minute demand and the floor. Subtracting a constant already-paid floor would not change the current minimizer. Carrying this state does not make MPC equivalent to a full-month optimizer. The report also includes a hinge penalty on exceedance above F. In SAA, C is cumulative delivered energy by the scenario departure and k is the corresponding energy requirement, conditioned on observed progress. Scenario weights sum to one for each EV. SAA is a convex expected-shortfall penalty, not a hard delivery guarantee.')

# 14
start('Additional results for comparison','Backup B · Service and cost','Report Tables III and VI • PV cases use net import for billing and peak')
text(40,123,880,'S2 · Monthly billing, known connected-driver requirements',18,True)
table(40,158,880,['Controller','Bill','Peak','Delivery'],[
('Uncontrolled — infeasible','$4,845','191.6 kW','100.1%'),('Earliest Deadline First','$4,116','144.0 kW','99.9%'),('Least Laxity First','$4,096','142.9 kW','99.9%')],[2.5,1,1,1],fs=14,row_h=38)
text(40,331,880,'S4 · Solar cases illustrating the cost / delivery trade-off',18,True)
table(40,366,880,['Controller / information','Bill','Peak','Delivery'],[
('Combined-QP / Median','$1,629','54.9 kW','75.8%'),('OPT-BILL-LP / Perfect','$1,675','55.1 kW','99.6%')],[2.5,1,1,1],fs=14,row_h=38)
note('This backup explains why low cost cannot be read alone. Uncontrolled charging violates network constraints, and its 100.1% delivery is the reported simulation value, not a target. Median Combined-QP with solar has a lower bill than the recommended online configuration but leaves about a quarter of requested energy undelivered. The offline OPT benchmark sees future arrivals and, in S4, known PV. It is not directly deployable as an online controller. All values are reported rather than independently reproduced in this editing session.')

# 15
start('References','Backup C · Sources','Primary source: the supplied 14-page project report, including appendices A–D')
refs=[
'Lee et al. (2021). Adaptive charging networks: A framework for smart electric vehicle charging. IEEE Transactions on Smart Grid, 12(5), 4339–4350.',
'Lee, Li & Low (2019). ACN-Data: Analysis and applications of an open EV charging dataset. ACM e-Energy, 139–149.',
'Lee et al. (2021). ACN-Sim: An open-source simulator for data-driven electric vehicle charging research. IEEE Transactions on Smart Grid, 12(6), 5113–5123.',
'Gan, Topcu & Low (2013). Optimal decentralized protocol for electric vehicle charging. IEEE Transactions on Power Systems, 28(2), 940–951.',
'Diamond & Boyd (2016). CVXPY: A Python-embedded modeling language for convex optimization. Journal of Machine Learning Research, 17(83), 1–5.',
'Goulart & Chen (2024). Clarabel: An interior-point solver for conic programs with quadratic objectives. arXiv:2405.12762.'
]
y=125
for i,r in enumerate(refs,1):
    text(40,y,25,str(i)+'.',13,True,ACCENT)
    hh=text(71,y,843,r,13,leading=19)
    y+=hh+11
note('References are taken from the supplied report. Related appendix reference: Bertsimas and Sim (2004), The price of robustness, Operations Research 52(1), 35–53. This revised presentation focuses on the monthly S2–S4 experiments rather than the exploratory S1 robustness extension. The figures and experiment results were not regenerated during presentation editing.')

pdf.save()
prs.save(OUT/'Grid-Aware Smart EV Charging - Revised.pptx')
(OUT/'Speaker notes.md').write_text('# Speaker notes\n\n12 main slides + 3 backup slides. Suggested main presentation: 10–12 minutes.\n\n'+'\n\n'.join(f'## Slide {n}\n\n{v}' for n,v in notes)+'\n')
print(f'Built {len(prs.slides)} slides; {len(boxes)} checked text boxes.')
