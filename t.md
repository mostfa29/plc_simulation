================================================================================
  DETAILED REGISTER ANALYSIS — 129.168.1.25:502
  Date: 2026-03-02 17:58:27
  Passes: 5 x 3.0s delay
================================================================================
  Connected to 129.168.1.25:502 (unit_id=0)

────────────────────────────────────────────────────────────────────────────────
  PHASE 1: Full register scan
────────────────────────────────────────────────────────────────────────────────

  Scanning R0-R500 (Main PLC registers (config, I/O, process data))...
    Found 148 non-zero registers out of 500 scanned

  Scanning R500-R1000 (Extended registers)...
    Found 101 non-zero registers out of 500 scanned

  Scanning R1000-R1500 (Extended I/O)...
    Found 20 non-zero registers out of 500 scanned

  Scanning R2000-R2200 (Encoder / counter registers)...
    Found 83 non-zero registers out of 200 scanned

  Scanning R3400-R3600 (RTC / diagnostics)...
    Found 91 non-zero registers out of 200 scanned

  Scanning R5000-R5600 (eWON tag registers)...
    Found 61 non-zero registers out of 600 scanned

  Scanning R6000-R6100 (Shop unit layout (R6000-6030))...
    Found 0 non-zero registers out of 100 scanned

  TOTAL: 504 non-zero registers across all ranges

────────────────────────────────────────────────────────────────────────────────
  PHASE 2: Taking 5 snapshots (3.0s apart)
────────────────────────────────────────────────────────────────────────────────
  Snapshot 1/5...
  Snapshot 2/5...
  Snapshot 3/5...
  Snapshot 4/5...
  Snapshot 5/5...
  Done. Collected 5 snapshots.

────────────────────────────────────────────────────────────────────────────────
  PHASE 3: Register analysis
────────────────────────────────────────────────────────────────────────────────

================================================================================
  REGISTER MAP — ALL NON-ZERO REGISTERS
  Legend: [C]=Changing  [S]=Static  GE=GE word-swap  STD=Standard IEEE
================================================================================

  Addr         Raw   Int16     Hex C/S   Range      GE Float32    STD Float32  Best Guess
  ──────── ─────── ─────── ─────── ─── ───────  ────────────── ──────────────  ──────────────────────────────
  R0            4       4 0x0004 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=4)
  R2            8       8 0x0008 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=8)
  R3           13      13 0x000D [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=13)
  R6        57857   -7679 0xE201 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | CONFIG/FW? (uint16=57857, constant)
  R7            6       6 0x0006 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=6)
  R8            8       8 0x0008 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=8)
  R9           10      10 0x000A [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=10)
  R10         257     257 0x0101 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TEMP_INT? (int16=257)
  R13           1       1 0x0001 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=1)
  R14        7500    7500 0x1D4C [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=7500)
  R18           2       2 0x0002 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=2)
  R31          25      25 0x0019 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=25)
  R34          25      25 0x0019 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=25)
  R37          25      25 0x0019 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=25)
  R40          25      25 0x0019 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=25)
  R43          25      25 0x0019 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=25)
  R46          25      25 0x0019 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=25)
  R49          25      25 0x0019 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=25)
  R52          25      25 0x0019 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=25)
  R55          25      25 0x0019 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=25)
  R61          25      25 0x0019 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=25)
  R64          25      25 0x0019 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=25)
  R67          25      25 0x0019 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=25)
  R70          25      25 0x0019 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=25)
  R73          25      25 0x0019 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=25)
  R76          25      25 0x0019 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=25)
  R79          25      25 0x0019 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=25)
  R82          25      25 0x0019 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=25)
  R85          25      25 0x0019 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=25)
  R88          25      25 0x0019 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=25)
  R91          25      25 0x0019 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=25)
  R94          25      25 0x0019 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=25)
  R97          25      25 0x0019 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=25)
  R100         25      25 0x0019 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=25)
  R103         50      50 0x0032 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=50)
  R107          5       5 0x0005 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=5)
  R108         50      50 0x0032 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=50)
  R110         25      25 0x0019 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=25)
  R118         10      10 0x000A [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=10)
  R124        100     100 0x0064 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=100)
  R130      20682   20682 0x50CA [S]       0         22.7894            ---  RPM_SETPOINT? (GE_f32=22.8, constant) | TORQUE_INT? or TARGET? (int16=20682)
  R131      16822   16822 0x41B6 [S]       0          0.0000        22.7500  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=16822)
  R140      57344   -8192 0xE000 [C]   49152       1055.0000            ---  TORQUE? (GE_f32=1055.0 ft-lbs, varies) | ENCODER/COUNTER? (range=49152)
  R141      17539   17539 0x4483 [C]       8          0.0000      1048.0000  RPM? (GE_f32=0.0, varies) | TORQUE_INT? or TARGET? (int16=17539)
  R145      12700   12700 0x319C [S]       0             ---         0.0000  TORQUE_INT? or TARGET? (int16=12700) | CONFIG/FW? (uint16=12700, constant)
  R146      21460   21460 0x53D4 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=21460)
  R151          5       5 0x0005 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=5)
  R152          5       5 0x0005 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=5)
  R153         76      76 0x004C [S]       0             ---         0.0000  RPM_INT? or COUNT? (int16=76) | TEMP_INT? (int16=76)
  R154      32767   32767 0x7FFF [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=32767)
  R155       1000    1000 0x03E8 [S]       0             ---         0.0000  TORQUE_INT? or TARGET? (int16=1000)
  R156      52660  -12876 0xCDB4 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | CONFIG/FW? (uint16=52660, constant)
  R157         46      46 0x002E [C]       1          0.0000         0.0000  RPM? (GE_f32=0.0, varies) | RPM_INT? or COUNT? (int16=46)
  R158       7360    7360 0x1CC0 [C]     160          0.0002         0.0000  RPM? (GE_f32=0.0, varies) | TORQUE_INT? or TARGET? (int16=7360)
  R159      14720   14720 0x3980 [C]     320          0.0000         0.0002  RPM? (GE_f32=0.0, varies) | TORQUE_INT? or TARGET? (int16=14720)
  R161      21460   21460 0x53D4 [S]       0      28713.9141            ---  TORQUE_SETPOINT? (GE_f32=28713.9 ft-lbs, constant) | TORQUE_INT? or TARGET? (int16=21460)
  R162      18144   18144 0x46E0 [C]      24         -2.0043     28768.0000  RPM? (GE_f32=-2.0, varies) | TORQUE_INT? or TARGET? (int16=18144)
  R163      49152  -16384 0xC000 [C]   12288      18144.0000        -2.0043  TORQUE? (GE_f32=18144.0 ft-lbs, varies) | ENCODER/COUNTER? (range=12288)
  R164      18061   18061 0x468D [S]       0          0.0000     18067.4551  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=18061)
  R165       9961    9961 0x26E9 [C]   61342          0.5670         0.0000  RPM? (GE_f32=0.6, varies) | TORQUE_INT? or TARGET? (int16=9961)
  R166      16145   16145 0x3F11 [C]       1             ---         0.5681  TORQUE_INT? or TARGET? (int16=16145)
  R167      28180   28180 0x6E14 [C]   14253        164.4300            ---  RPM? (GE_f32=164.4, varies) | TORQUE_INT? or TARGET? (int16=28180)
  R168      17188   17188 0x4324 [S]       0             ---       164.8600  TORQUE_INT? or TARGET? (int16=17188) | CONFIG/FW? (uint16=17188, constant)
  R169      56360   -9176 0xDC28 [C]   28506        124.4300            ---  RPM? (GE_f32=124.4, varies) | ENCODER/COUNTER? (range=28506)
  R170      17144   17144 0x42F8 [S]       0          0.0000       124.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=17144)
  R173      65531      -5 0xFFFB [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | CONFIG/FW? (uint16=65531, constant)
  R175          5       5 0x0005 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=5)
  R178      17297   17297 0x4391 [S]       0          0.0000       290.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=17297)
  R180      16928   16928 0x4220 [S]       0          0.0000        40.0002  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=16928)
  R181         46      46 0x002E [C]       1          0.0000         0.0000  RPM? (GE_f32=0.0, varies) | RPM_INT? or COUNT? (int16=46)
  R185         20      20 0x0014 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=20)
  R187       6621    6621 0x19DD [C]     595          0.0000         0.0000  RPM? (GE_f32=0.0, varies) | TORQUE_INT? or TARGET? (int16=6621)
  R188          5       5 0x0005 [S]       0             ---         0.0000  STATE/MODE? (int16=5) | RPM_INT? or COUNT? (int16=5)
  R189      24576   24576 0x6000 [C]   36864      19376.0000            ---  TORQUE? (GE_f32=19376.0 ft-lbs, varies) | TORQUE_INT? or TARGET? (int16=24576)
  R190      18071   18071 0x4697 [C]       1          0.0000     19328.0000  RPM? (GE_f32=0.0, varies) | TORQUE_INT? or TARGET? (int16=18071)
  R192      17747   17747 0x4553 [C]      12          0.0000      3376.0000  RPM? (GE_f32=0.0, varies) | TORQUE_INT? or TARGET? (int16=17747)
  R195       4194    4194 0x1062 [C]   52429          0.1055         0.0000  RPM? (GE_f32=0.1, varies) | TORQUE_INT? or TARGET? (int16=4194)
  R196      15832   15832 0x3DD8 [C]      12             ---         0.1060  TORQUE_INT? or TARGET? (int16=15832)
  R197      65531      -5 0xFFFB [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | CONFIG/FW? (uint16=65531, constant)
  R206          5       5 0x0005 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=5)
  R207          5       5 0x0005 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=5)
  R208       1012    1012 0x03F4 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=1012)
  R212         30      30 0x001E [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=30)
  R219          5       5 0x0005 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=5)
  R222          5       5 0x0005 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=5)
  R226      21460   21460 0x53D4 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=21460)
  R228      12024   12024 0x2EF8 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=12024)
  R246         30      30 0x001E [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=30)
  R252         50      50 0x0032 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=50)
  R256      51872  -13664 0xCAA0 [C]   53216     335445.0000            ---  ENCODER/COUNTER? (range=53216)
  R257      18595   18595 0x48A3 [C]      19             ---    334420.8125  TORQUE_INT? or TARGET? (int16=18595)
  R258      19098   19098 0x4A9A [C]   43142     709801.6250            ---  TORQUE_INT? or TARGET? (int16=19098) | ENCODER/COUNTER? (range=43142)
  R259      18733   18733 0x492D [C]      20             ---    710083.5000  TORQUE_INT? or TARGET? (int16=18733)
  R260      23608   23608 0x5C38 [C]   46936     771523.5000            ---  TORQUE_INT? or TARGET? (int16=23608) | ENCODER/COUNTER? (range=46936)
  R261      18748   18748 0x493C [C]      21          0.0000    770274.1875  RPM? (GE_f32=0.0, varies) | TORQUE_INT? or TARGET? (int16=18748)
  R262       3619    3619 0x0E23 [C]   38201      29575.0684         0.0000  TORQUE? (GE_f32=29575.1 ft-lbs, varies) | TORQUE_INT? or TARGET? (int16=3619)
  R263      18151   18151 0x46E7 [C]      26             ---     29608.7969  TORQUE_INT? or TARGET? (int16=18151)
  R264      20888   20888 0x5198 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=20888)
  R266       6189    6189 0x182D [C]   54584       9414.0439         0.0000  TORQUE? (GE_f32=9414.0 ft-lbs, varies) | TORQUE_INT? or TARGET? (int16=6189)
  R267      17939   17939 0x4613 [C]      17          0.0000      9408.0000  RPM? (GE_f32=0.0, varies) | TORQUE_INT? or TARGET? (int16=17939)
  R270      23010   23010 0x59E2 [C]   25356       7531.2354            ---  TORQUE? (GE_f32=7531.2 ft-lbs, varies) | TORQUE_INT? or TARGET? (int16=23010)
  R271      17899   17899 0x45EB [C]      26          0.0000      7520.0000  RPM? (GE_f32=0.0, varies) | TORQUE_INT? or TARGET? (int16=17899)
  R293      31730   31730 0x7BF2 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=31730)
  R297         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R298         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R299         95      95 0x005F [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=95)
  R301         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R303      32767   32767 0x7FFF [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=32767)
  R304         10      10 0x000A [S]       0             ---         0.0000  STATE/MODE? (int16=10) | RPM_INT? or COUNT? (int16=10)
  R305      64776    -760 0xFD08 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | CONFIG/FW? (uint16=64776, constant)
  R306       4701    4701 0x125D [S]       0          1.5865         0.0000  RPM_SETPOINT? (GE_f32=1.6, constant) | TORQUE_INT? or TARGET? (int16=4701)
  R307      16331   16331 0x3FCB [S]       0             ---         1.5929  TORQUE_INT? or TARGET? (int16=16331) | CONFIG/FW? (uint16=16331, constant)
  R308      58347   -7189 0xE3EB [S]       0      31729.9590            ---  TORQUE_SETPOINT? (GE_f32=31730.0 ft-lbs, constant) | CONFIG/FW? (uint16=58347, constant)
  R309      18167   18167 0x46F7 [S]       0          0.0000     31616.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=18167)
  R313          5       5 0x0005 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=5)
  R316          5       5 0x0005 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=5)
  R370      18022   18022 0x4666 [C]       5          0.0000     14720.0000  RPM? (GE_f32=0.0, varies) | TORQUE_INT? or TARGET? (int16=18022)
  R380      49696  -15840 0xC220 [S]       0          0.0000       -40.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | CONFIG/FW? (uint16=49696, constant)
  R382         20      20 0x0014 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=20)
  R385         20      20 0x0014 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=20)
  R387      32767   32767 0x7FFF [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=32767)
  R388       3000    3000 0x0BB8 [S]       0             ---         0.0000  TORQUE_INT? or TARGET? (int16=3000)
  R389      63921   -1615 0xF9B1 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | CONFIG/FW? (uint16=63921, constant)
  R392      17633   17633 0x44E1 [S]       0          0.0000      1800.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=17633)
  R394         20      20 0x0014 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=20)
  R396      32767   32767 0x7FFF [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=32767)
  R397       3000    3000 0x0BB8 [S]       0             ---         0.0000  TORQUE_INT? or TARGET? (int16=3000)
  R398      63922   -1614 0xF9B2 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | CONFIG/FW? (uint16=63922, constant)
  R400          3       3 0x0003 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=3)
  R417      32768  -32768 0x8000 [S]       0        321.0000        -0.0000  TEMPERATURE? (GE_f32=321.0, constant) | CONFIG/FW? (uint16=32768, constant)
  R418      17312   17312 0x43A0 [S]       0          0.0000       320.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=17312)
  R431      16952   16952 0x4238 [C]       4             ---        46.0757  TORQUE_INT? or TARGET? (int16=16952)
  R432      19832   19832 0x4D78 [C]      24             ---            ---  TORQUE_INT? or TARGET? (int16=19832)
  R433      61440   -4096 0xF000 [C]   12288      19832.0000            ---  TORQUE? (GE_f32=19832.0 ft-lbs, varies) | ENCODER/COUNTER? (range=12288)
  R434      18074   18074 0x469A [S]       0         -0.0000     19795.9688  RPM_SETPOINT? (GE_f32=-0.0, constant) | TORQUE_INT? or TARGET? (int16=18074)
  R435      42992  -22544 0xA7F0 [C]   12583          0.6198        -0.0000  RPM? (GE_f32=0.6, varies) | ENCODER/COUNTER? (range=12583)
  R436      16158   16158 0x3F1E [S]       0         -0.0007         0.6200  RPM_SETPOINT? (GE_f32=-0.0, constant) | TORQUE_INT? or TARGET? (int16=16158)
  R437      47678  -17858 0xBA3E [C]   14254        179.7275        -0.0007  RPM? (GE_f32=179.7, varies) | ENCODER/COUNTER? (range=14254)
  R438      17203   17203 0x4333 [S]       0         -0.0007       179.7275  RPM_SETPOINT? (GE_f32=-0.0, constant) | TORQUE_INT? or TARGET? (int16=17203)
  R439      47678  -17858 0xBA3E [C]   14254        139.7275        -0.0007  RPM? (GE_f32=139.7, varies) | ENCODER/COUNTER? (range=14254)
  R440      17163   17163 0x430B [S]       0          0.0000       139.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=17163)
  R442          5       5 0x0005 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=5)
  R478       5000    5000 0x1388 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=5000)
  R483      32767   32767 0x7FFF [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=32767)
  R484        300     300 0x012C [S]       0             ---         0.0000  TEMP_INT? (int16=300)
  R485      63921   -1615 0xF9B1 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | CONFIG/FW? (uint16=63921, constant)
  R498      43008  -22528 0xA800 [S]       0      21460.0000        -0.0000  TORQUE_SETPOINT? (GE_f32=21460.0 ft-lbs, constant) | CONFIG/FW? (uint16=43008, constant)
  R499      18087   18087 0x46A7 [S]       0          0.0000     21376.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=18087)
  R502      52782  -12754 0xCE2E [C]   54561       8089.7725            ---  TORQUE? (GE_f32=8089.8 ft-lbs, varies) | ENCODER/COUNTER? (range=54561)
  R503      17916   17916 0x45FC [C]      35         -0.0000      8080.0000  RPM? (GE_f32=-0.0, varies) | TORQUE_INT? or TARGET? (int16=17916)
  R505      17888   17888 0x45E0 [C]      38          0.0000      7171.5078  RPM? (GE_f32=0.0, varies) | TORQUE_INT? or TARGET? (int16=17888)
  R506       7184    7184 0x1C10 [C]    1200             ---         0.0000  TORQUE_INT? or TARGET? (int16=7184) | ENCODER/COUNTER? (range=1200)
  R507      28597   28597 0x6FB5 [C]   40137          0.2528            ---  RPM? (GE_f32=0.3, varies) | TORQUE_INT? or TARGET? (int16=28597)
  R508      16001   16001 0x3E81 [C]      33          0.0000         0.2520  RPM? (GE_f32=0.0, varies) | TORQUE_INT? or TARGET? (int16=16001)
  R511      58196   -7340 0xE354 [C]   44565          0.2245            ---  RPM? (GE_f32=0.2, varies) | ENCODER/COUNTER? (range=44565)
  R512      15973   15973 0x3E65 [C]      38          0.0000         0.2236  RPM? (GE_f32=0.0, varies) | TORQUE_INT? or TARGET? (int16=15973)
  R517       3000    3000 0x0BB8 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=3000)
  R520      17530   17530 0x447A [S]       0          0.0000      1000.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=17530)
  R522      17633   17633 0x44E1 [S]       0          2.0042      1802.0000  RPM_SETPOINT? (GE_f32=2.0, constant) | TORQUE_INT? or TARGET? (int16=17633)
  R523      16384   16384 0x4000 [S]       0       1250.0000         2.0042  TORQUE_SETPOINT? (GE_f32=1250.0 ft-lbs, constant) | TORQUE_INT? or TARGET? (int16=16384)
  R524      17564   17564 0x449C [S]       0          2.0042      1250.0000  RPM_SETPOINT? (GE_f32=2.0, constant) | TORQUE_INT? or TARGET? (int16=17564)
  R525      16384   16384 0x4000 [S]       0       1250.0000         2.0042  TORQUE_SETPOINT? (GE_f32=1250.0 ft-lbs, constant) | TORQUE_INT? or TARGET? (int16=16384)
  R526      17564   17564 0x449C [S]       0          0.0000      1248.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=17564)
  R528      17633   17633 0x44E1 [S]       0          0.0000      1800.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=17633)
  R530      17549   17549 0x448D [S]       0          0.0000      1128.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=17549)
  R532      17633   17633 0x44E1 [S]       0          0.0000      1800.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=17633)
  R534         50      50 0x0032 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=50)
  R537        500     500 0x01F4 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant)
  R540         10      10 0x000A [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=10)
  R543         10      10 0x000A [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=10)
  R546         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R549         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R552         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R555         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R558         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R561         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R564         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R567         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R570         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R573         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R576         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R579         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R582         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R585         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R588         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R591         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R594         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R597         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R599         15      15 0x000F [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=15)
  R607      42916  -22620 0xA7A4 [C]   56707       8233.9102        -0.0000  TORQUE? (GE_f32=8233.9 ft-lbs, varies) | ENCODER/COUNTER? (range=56707)
  R608      17920   17920 0x4600 [C]      23          0.0000      8192.0000  RPM? (GE_f32=0.0, varies) | TORQUE_INT? or TARGET? (int16=17920)
  R610         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R613         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R616         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R619         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R622         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R625         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R628         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R632      49152  -16384 0xC000 [S]       0      28000.0000        -2.0043  TORQUE_SETPOINT? (GE_f32=28000.0 ft-lbs, constant) | CONFIG/FW? (uint16=49152, constant)
  R633      18138   18138 0x46DA [S]       0          0.0000     27904.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=18138)
  R642      40960  -24576 0xA000 [S]       0      18000.0000        -0.0000  TORQUE_SETPOINT? (GE_f32=18000.0 ft-lbs, constant) | CONFIG/FW? (uint16=40960, constant)
  R643      18060   18060 0x468C [S]       0          0.0000     17926.8359  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=18060)
  R644       3500    3500 0x0DAC [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=3500)
  R646         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R649         80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R655       3500    3500 0x0DAC [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=3500)
  R656       1000    1000 0x03E8 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=1000)
  R658         50      50 0x0032 [S]       0             ---         0.0000  RPM_INT? or COUNT? (int16=50)
  R659      65486     -50 0xFFCE [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | CONFIG/FW? (uint16=65486, constant)
  R677        100     100 0x0064 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=100)
  R679         50      50 0x0032 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=50)
  R682         50      50 0x0032 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=50)
  R685         30      30 0x001E [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=30)
  R688         30      30 0x001E [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=30)
  R691         10      10 0x000A [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=10)
  R693          3       3 0x0003 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=3)
  R694          3       3 0x0003 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=3)
  R695         24      24 0x0018 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=24)
  R697         30      30 0x001E [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=30)
  R700         30      30 0x001E [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=30)
  R703         30      30 0x001E [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=30)
  R706         30      30 0x001E [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=30)
  R709         30      30 0x001E [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=30)
  R712         30      30 0x001E [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=30)
  R715         30      30 0x001E [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=30)
  R718         30      30 0x001E [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=30)
  R721         30      30 0x001E [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=30)
  R724         30      30 0x001E [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=30)
  R727         30      30 0x001E [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=30)
  R730         30      30 0x001E [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=30)
  R863      16822   16822 0x41B6 [S]       0          0.0000        22.7501  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=16822)
  R864         50      50 0x0032 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=50)
  R865         50      50 0x0032 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=50)
  R866        344     344 0x0158 [S]       0          0.7891         0.0000  RPM_SETPOINT? (GE_f32=0.8, constant) | TEMP_INT? (int16=344)
  R867      16202   16202 0x3F4A [S]       0          0.0000         0.7891  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=16202)
  R883          2       2 0x0002 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=2)
  R884          2       2 0x0002 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=2)
  R885       1503    1503 0x05DF [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=1503)
  R897      65411    -125 0xFF83 [S]       0        619.9924            ---  TORQUE_SETPOINT? (GE_f32=620.0 ft-lbs, constant) | TEMP_INT? (int16=-125)
  R898      17434   17434 0x441A [S]       0          0.7901       616.9889  RPM_SETPOINT? (GE_f32=0.8, constant) | TORQUE_INT? or TARGET? (int16=17434)
  R899      16202   16202 0x3F4A [S]       0          0.0000         0.7891  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=16202)
  R925       3000    3000 0x0BB8 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=3000)
  R928          4       4 0x0004 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=4)
  R931      32767   32767 0x7FFF [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=32767)
  R932          5       5 0x0005 [S]       0             ---         0.0000  STATE/MODE? (int16=5) | RPM_INT? or COUNT? (int16=5)
  R933      64500   -1036 0xFBF4 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | CONFIG/FW? (uint16=64500, constant)
  R934          8       8 0x0008 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=8)
  R935          8       8 0x0008 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=8)
  R936        151     151 0x0097 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=151)
  R1151         4       4 0x0004 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=4)
  R1153        30      30 0x001E [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=30)
  R1154        30      30 0x001E [S]       0             ---         0.0000  RPM_INT? or COUNT? (int16=30)
  R1155     65506     -30 0xFFE2 [S]       0             ---            ---  CONFIG/FW? (uint16=65506, constant) | STATUS_FLAG? (int16=-30, constant)
  R1156     65507     -29 0xFFE3 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | CONFIG/FW? (uint16=65507, constant)
  R1157         3       3 0x0003 [S]       0             ---         0.0000  STATE/MODE? (int16=3) | RPM_INT? or COUNT? (int16=3)
  R1158     65535      -1 0xFFFF [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | CONFIG/FW? (uint16=65535, constant)
  R1159        31      31 0x001F [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=31)
  R1160         1       1 0x0001 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=1)
  R1162       100     100 0x0064 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=100)
  R1236         8       8 0x0008 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=8)
  R1238         5       5 0x0005 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=5)
  R1239      6144    6144 0x1800 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=6144)
  R1241         2       2 0x0002 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=2)
  R1271     40960  -24576 0xA000 [S]       0      18000.0000        -0.0000  TORQUE_SETPOINT? (GE_f32=18000.0 ft-lbs, constant) | CONFIG/FW? (uint16=40960, constant)
  R1272     18060   18060 0x468C [S]       0          0.0000     17920.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=18060)
  R1371     20682   20682 0x50CA [S]       0         22.7894            ---  RPM_SETPOINT? (GE_f32=22.8, constant) | TORQUE_INT? or TARGET? (int16=20682)
  R1372     16822   16822 0x41B6 [S]       0          0.0000        22.7500  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=16822)
  R1449        80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R1452        80      80 0x0050 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=80)
  R2000     20968   20968 0x51E8 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=20968)
  R2002      4694    4694 0x1256 [S]       0             ---         0.0000  TORQUE_INT? or TARGET? (int16=4694)
  R2003     20968   20968 0x51E8 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=20968)
  R2004      2348    2348 0x092C [S]       0             ---         0.0000  TORQUE_INT? or TARGET? (int16=2348)
  R2005     20968   20968 0x51E8 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=20968)
  R2007     21192   21192 0x52C8 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=21192)
  R2008        78      78 0x004E [S]       0             ---         0.0000  RPM_INT? or COUNT? (int16=78) | TEMP_INT? (int16=78)
  R2009     20968   20968 0x51E8 [S]       0             ---            ---  TORQUE_INT? or TARGET? (int16=20968) | CONFIG/FW? (uint16=20968, constant)
  R2010     62526   -3010 0xF43E [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | CONFIG/FW? (uint16=62526, constant)
  R2011        15      15 0x000F [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=15)
  R2012      5850    5850 0x16DA [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=5850)
  R2027         1       1 0x0001 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=1)
  R2028         1       1 0x0001 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=1)
  R2029        65      65 0x0041 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=65)
  R2031      6000    6000 0x1770 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=6000)
  R2032        10      10 0x000A [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=10)
  R2033     13700   13700 0x3584 [S]       0          0.0027         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=13700)
  R2034     15151   15151 0x3B2F [S]       0             ---         0.0027  TORQUE_INT? or TARGET? (int16=15151) | CONFIG/FW? (uint16=15151, constant)
  R2035     32000   32000 0x7D00 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=32000)
  R2036     12700   12700 0x319C [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=12700)
  R2037      1806    1806 0x070E [S]       0          0.0134         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=1806)
  R2038     15451   15451 0x3C5B [S]       0          0.0000         0.0134  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=15451)
  R2039     13420   13420 0x346C [S]       0          0.0075         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=13420)
  R2040     15351   15351 0x3BF7 [S]       0          0.0000         0.0075  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=15351)
  R2042     20682   20682 0x50CA [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=20682)
  R2046       108     108 0x006C [S]       0          0.0134         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=108)
  R2047     15451   15451 0x3C5B [S]       0          0.0000         0.0134  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=15451)
  R2049       500     500 0x01F4 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant)
  R2051     32000   32000 0x7D00 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=32000)
  R2054         2       2 0x0002 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=2)
  R2056        12      12 0x000C [S]       0             ---         0.0000  STATE/MODE? (int16=12) | RPM_INT? or COUNT? (int16=12)
  R2057     65524     -12 0xFFF4 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | CONFIG/FW? (uint16=65524, constant)
  R2058      1000    1000 0x03E8 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=1000)
  R2060      1000    1000 0x03E8 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=1000)
  R2062      5000    5000 0x1388 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=5000)
  R2063       500     500 0x01F4 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant)
  R2066       500     500 0x01F4 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant)
  R2067         4       4 0x0004 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=4)
  R2069       500     500 0x01F4 [S]       0             ---         0.0000  UNKNOWN (uint16=500, int16=500)
  R2070     29300   29300 0x7274 [C]   25194          0.0000            ---  RPM? (GE_f32=0.0, varies) | TORQUE_INT? or TARGET? (int16=29300)
  R2071       500     500 0x01F4 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant)
  R2073     29792   29792 0x7460 [C]   37664          0.0000            ---  RPM? (GE_f32=0.0, varies) | TORQUE_INT? or TARGET? (int16=29792)
  R2074      4478    4478 0x117E [C]    3844          0.0000         0.0000  RPM? (GE_f32=0.0, varies) | TORQUE_INT? or TARGET? (int16=4478)
  R2075       500     500 0x01F4 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant)
  R2076      6223    6223 0x184F [C]      37          0.0000         0.0000  RPM? (GE_f32=0.0, varies) | TORQUE_INT? or TARGET? (int16=6223)
  R2077        16      16 0x0010 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=16)
  R2078      4434    4434 0x1152 [C]    6771          0.0000         0.0000  RPM? (GE_f32=0.0, varies) | TORQUE_INT? or TARGET? (int16=4434)
  R2096         2       2 0x0002 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=2)
  R2100       800     800 0x0320 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=800)
  R2102      2000    2000 0x07D0 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=2000)
  R2104     16822   16822 0x41B6 [S]       0          0.0000        22.7748  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=16822)
  R2105     13000   13000 0x32C8 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=13000)
  R2107         1       1 0x0001 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=1)
  R2108     13000   13000 0x32C8 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=13000)
  R2110     60842   -4694 0xEDAA [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | CONFIG/FW? (uint16=60842, constant)
  R2111     13000   13000 0x32C8 [S]       0             ---         0.0000  TORQUE_INT? or TARGET? (int16=13000) | CONFIG/FW? (uint16=13000, constant)
  R2112     60934   -4602 0xEE06 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | CONFIG/FW? (uint16=60934, constant)
  R2113     13000   13000 0x32C8 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=13000)
  R2115      8768    8768 0x2240 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=8768)
  R2116       187     187 0x00BB [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=187)
  R2117     13000   13000 0x32C8 [S]       0             ---         0.0000  TORQUE_INT? or TARGET? (int16=13000) | CONFIG/FW? (uint16=13000, constant)
  R2118     62510   -3026 0xF42E [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | CONFIG/FW? (uint16=62510, constant)
  R2119        15      15 0x000F [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=15)
  R2120      8932    8932 0x22E4 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=8932)
  R2136         5       5 0x0005 [S]       0         -2.0000         0.0000  RPM_SETPOINT? (GE_f32=-2.0, constant) | STATE/MODE? (int16=5)
  R2137     49152  -16384 0xC000 [S]       0          0.0000        -2.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | CONFIG/FW? (uint16=49152, constant)
  R2139        55      55 0x0037 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=55)
  R2140        55      55 0x0037 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=55)
  R2141       891     891 0x037B [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=891)
  R2143         5       5 0x0005 [S]       0         -2.0000         0.0000  RPM_SETPOINT? (GE_f32=-2.0, constant) | STATE/MODE? (int16=5)
  R2144     49152  -16384 0xC000 [S]       0          0.0000        -2.0032  RPM_SETPOINT? (GE_f32=0.0, constant) | CONFIG/FW? (uint16=49152, constant)
  R2145     13400   13400 0x3458 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=13400)
  R2147        50      50 0x0032 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=50)
  R2149     12600   12600 0x3138 [S]       0             ---         0.0000  TORQUE_INT? or TARGET? (int16=12600) | CONFIG/FW? (uint16=12600, constant)
  R2150     32000   32000 0x7D00 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=32000)
  R2152        25      25 0x0019 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=25)
  R2153        25      25 0x0019 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=25)
  R2154       514     514 0x0202 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=514)
  R2194     17588   17588 0x44B4 [S]       0          0.0000      1440.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=17588)
  R2196     17588   17588 0x44B4 [S]       0          0.0000      1440.0009  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=17588)
  R2197         7       7 0x0007 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=7)
  R2198         7       7 0x0007 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=7)
  R2199       102     102 0x0066 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=102)
  R3400     28160   28160 0x6E00 [S]       0      21175.0000            ---  TORQUE_SETPOINT? (GE_f32=21175.0 ft-lbs, constant) | TORQUE_INT? or TARGET? (int16=28160)
  R3401     18085   18085 0x46A5 [S]       0             ---     21164.0000  TORQUE_INT? or TARGET? (int16=18085) | CONFIG/FW? (uint16=18085, constant)
  R3402     22528   22528 0x5800 [S]       0      16940.0000            ---  TORQUE_SETPOINT? (GE_f32=16940.0 ft-lbs, constant) | TORQUE_INT? or TARGET? (int16=22528)
  R3403     18052   18052 0x4684 [S]       0        617.1018     16930.0508  TORQUE_SETPOINT? (GE_f32=617.1 ft-lbs, constant) | TORQUE_INT? or TARGET? (int16=18052)
  R3404     17434   17434 0x441A [S]       0          0.0000       616.8433  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=17434)
  R3405     13816   13816 0x35F8 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=13816)
  R3407     13000   13000 0x32C8 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=13000)
  R3408     13312   13312 0x3400 [S]       0      17434.0000         0.0000  TORQUE_SETPOINT? (GE_f32=17434.0 ft-lbs, constant) | TORQUE_INT? or TARGET? (int16=13312)
  R3409     18056   18056 0x4688 [S]       0          0.0000     17434.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=18056)
  R3410     13312   13312 0x3400 [S]       0      17434.0000         0.0000  TORQUE_SETPOINT? (GE_f32=17434.0 ft-lbs, constant) | TORQUE_INT? or TARGET? (int16=13312)
  R3411     18056   18056 0x4688 [S]       0             ---     17518.0000  TORQUE_INT? or TARGET? (int16=18056) | CONFIG/FW? (uint16=18056, constant)
  R3412     56320   -9216 0xDC00 [S]       0      15351.0000            ---  TORQUE_SETPOINT? (GE_f32=15351.0 ft-lbs, constant) | CONFIG/FW? (uint16=56320, constant)
  R3413     18031   18031 0x466F [S]       0             ---     15351.8994  TORQUE_INT? or TARGET? (int16=18031) | CONFIG/FW? (uint16=18031, constant)
  R3414     57241   -8295 0xDF99 [S]       0      13815.8994            ---  TORQUE_SETPOINT? (GE_f32=13815.9 ft-lbs, constant) | CONFIG/FW? (uint16=57241, constant)
  R3415     18007   18007 0x4657 [S]       0          0.0000     13760.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=18007)
  R3420      8192    8192 0x2000 [S]       0      13000.0000         0.0000  TORQUE_SETPOINT? (GE_f32=13000.0 ft-lbs, constant) | TORQUE_INT? or TARGET? (int16=8192)
  R3421     17995   17995 0x464B [S]       0          0.0000     13000.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=17995)
  R3422      8192    8192 0x2000 [S]       0      13000.0000         0.0000  TORQUE_SETPOINT? (GE_f32=13000.0 ft-lbs, constant) | TORQUE_INT? or TARGET? (int16=8192)
  R3423     17995   17995 0x464B [S]       0          0.0000     12992.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=17995)
  R3425         5       5 0x0005 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=5)
  R3427     28672   28672 0x7000 [S]       0      12700.0000            ---  TORQUE_SETPOINT? (GE_f32=12700.0 ft-lbs, constant) | TORQUE_INT? or TARGET? (int16=28672)
  R3428     17990   17990 0x4646 [S]       0          0.0000     12672.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=17990)
  R3430        47      47 0x002F [C]       2          0.0000         0.0000  RPM? (GE_f32=0.0, varies) | RPM_INT? or COUNT? (int16=47)
  R3437     49152  -16384 0xC000 [S]       0      10160.0000        -2.0043  TORQUE_SETPOINT? (GE_f32=10160.0 ft-lbs, constant) | CONFIG/FW? (uint16=49152, constant)
  R3438     17950   17950 0x461E [S]       0          0.0000     10121.9219  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=17950)
  R3439     10160   10160 0x27B0 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=10160)
  R3441     28672   28672 0x7000 [S]       0      19128.0000            ---  TORQUE_SETPOINT? (GE_f32=19128.0 ft-lbs, constant) | TORQUE_INT? or TARGET? (int16=28672)
  R3442     18069   18069 0x4695 [S]       0          0.0000     19072.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=18069)
  R3445         3       3 0x0003 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=3)
  R3446      2026    2026 0x07EA [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=2026)
  R3448         2       2 0x0002 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=2)
  R3449         2       2 0x0002 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=2)
  R3450        17      17 0x0011 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=17)
  R3451        44      44 0x002C [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=44)
  R3452         5       5 0x0005 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=5)
  R3453         5       5 0x0005 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=5)
  R3454      1635    1635 0x0663 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=1635)
  R3457        10      10 0x000A [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=10)
  R3460        10      10 0x000A [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=10)
  R3468     18434   18434 0x4802 [S]       0          0.0000    133332.6250  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=18434)
  R3469     13608   13608 0x3528 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=13608)
  R3470         5       5 0x0005 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=5)
  R3471         5       5 0x0005 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=5)
  R3472      1635    1635 0x0663 [S]       0             ---         0.0000  TORQUE_INT? or TARGET? (int16=1635)
  R3473     32767   32767 0x7FFF [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=32767)
  R3474        15      15 0x000F [S]       0             ---         0.0000  RPM_INT? or COUNT? (int16=15)
  R3475     64023   -1513 0xFA17 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | CONFIG/FW? (uint16=64023, constant)
  R3477        15      15 0x000F [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=15)
  R3479     20480   20480 0x5000 [S]       0       3013.0000            ---  TORQUE_SETPOINT? (GE_f32=3013.0 ft-lbs, constant) | TORQUE_INT? or TARGET? (int16=20480)
  R3480     17724   17724 0x453C [S]       0          0.0000      3008.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=17724)
  R3494         2       2 0x0002 [S]       0             ---         0.0000  STATE/MODE? (int16=2) | RPM_INT? or COUNT? (int16=2)
  R3495     65534      -2 0xFFFE [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | CONFIG/FW? (uint16=65534, constant)
  R3496      1000    1000 0x03E8 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=1000)
  R3500      9845    9845 0x2675 [S]       0         22.7688         0.0000  RPM_SETPOINT? (GE_f32=22.8, constant) | TORQUE_INT? or TARGET? (int16=9845)
  R3501     16822   16822 0x41B6 [S]       0          0.0000        22.7500  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=16822)
  R3514        21      21 0x0015 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=21)
  R3516       517     517 0x0205 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=517)
  R3531     16822   16822 0x41B6 [S]       0         17.9071        22.7820  RPM_SETPOINT? (GE_f32=17.9, constant) | TORQUE_INT? or TARGET? (int16=16822)
  R3532     16783   16783 0x418F [S]       0         17.9070        17.9070  RPM_SETPOINT? (GE_f32=17.9, constant) | TORQUE_INT? or TARGET? (int16=16783)
  R3533     16783   16783 0x418F [S]       0         22.7820        17.9071  RPM_SETPOINT? (GE_f32=22.8, constant) | TORQUE_INT? or TARGET? (int16=16783)
  R3534     16822   16822 0x41B6 [S]       0          0.0000        22.7748  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=16822)
  R3535     13000   13000 0x32C8 [S]       0          0.3402         0.0000  RPM_SETPOINT? (GE_f32=0.3, constant) | TORQUE_INT? or TARGET? (int16=13000)
  R3536     16046   16046 0x3EAE [S]       0     483829.4375         0.3404  TORQUE_INT? or TARGET? (int16=16046) | CONFIG/FW? (uint16=16046, constant)
  R3537     18668   18668 0x48EC [S]       0             ---    483923.6875  TORQUE_INT? or TARGET? (int16=18668) | CONFIG/FW? (uint16=18668, constant)
  R3538     19062   19062 0x4A76 [S]       0          0.0129            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=19062)
  R3539     15443   15443 0x3C53 [C]   18228         24.1545         0.0129  RPM? (GE_f32=24.2, varies) | TORQUE_INT? or TARGET? (int16=15443)
  R3540     16833   16833 0x41C1 [C]      29          0.0000        24.1251  RPM? (GE_f32=0.0, varies) | TORQUE_INT? or TARGET? (int16=16833)
  R3541        45      45 0x002D [S]       0             ---         0.0000  RPM_INT? or COUNT? (int16=45)
  R3542     20968   20968 0x51E8 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=20968)
  R3543      4251    4251 0x109B [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=4251)
  R3544         5       5 0x0005 [S]       0             ---         0.0000  STATE/MODE? (int16=5) | RPM_INT? or COUNT? (int16=5)
  R3545     26624   26624 0x6800 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=26624)
  R3547         3       3 0x0003 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=3)
  R3548      6144    6144 0x1800 [S]       0          0.0075         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=6144)
  R3549     15351   15351 0x3BF7 [S]       0          0.0000         0.0075  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=15351)
  R3550     12700   12700 0x319C [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=12700)
  R3552      6656    6656 0x1A00 [S]       0      22157.0000         0.0000  TORQUE_SETPOINT? (GE_f32=22157.0 ft-lbs, constant) | TORQUE_INT? or TARGET? (int16=6656)
  R3553     18093   18093 0x46AD [S]       0             ---     22185.3184  TORQUE_INT? or TARGET? (int16=18093) | CONFIG/FW? (uint16=18093, constant)
  R3554     21155   21155 0x52A3 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=21155)
  R3558     28804   28804 0x7084 [C]   11947         46.6099            ---  RPM? (GE_f32=46.6, varies) | TORQUE_INT? or TARGET? (int16=28804)
  R3559     16954   16954 0x423A [C]       6             ---        46.5781  TORQUE_INT? or TARGET? (int16=16954)
  R3560     20480   20480 0x5000 [S]       0      25000.0000            ---  TORQUE_SETPOINT? (GE_f32=25000.0 ft-lbs, constant) | TORQUE_INT? or TARGET? (int16=20480)
  R3561     18115   18115 0x46C3 [S]       0          0.0000     24960.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=18115)
  R3584     30481   30481 0x7711 [C]   25198             ---            ---  TORQUE_INT? or TARGET? (int16=30481) | ENCODER/COUNTER? (range=25198)
  R3585     65520     -16 0xFFF0 [S]       0             ---            ---  CONFIG/FW? (uint16=65520, constant) | STATUS_FLAG? (int16=-16, constant)
  R3586     64377   -1159 0xFB79 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | CONFIG/FW? (uint16=64377, constant)
  R3587        11      11 0x000B [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=11)
  R3588        15      15 0x000F [S]       0             ---         0.0000  RPM_INT? or COUNT? (int16=15)
  R3589     30481   30481 0x7711 [C]   25198          0.0000            ---  RPM? (GE_f32=0.0, varies) | TORQUE_INT? or TARGET? (int16=30481)
  R3590      8711    8711 0x2207 [C]    1226             ---         0.0000  TORQUE_INT? or TARGET? (int16=8711) | ENCODER/COUNTER? (range=1226)
  R3591     30481   30481 0x7711 [C]   25198          0.0000            ---  RPM? (GE_f32=0.0, varies) | TORQUE_INT? or TARGET? (int16=30481)
  R5106      2413    2413 0x096D [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=2413)
  R5108     64536   -1000 0xFC18 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | CONFIG/FW? (uint16=64536, constant)
  R5230         2       2 0x0002 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=2)
  R5231        24      24 0x0018 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=24)
  R5232      2048    2048 0x0800 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=2048)
  R5234     14710   14710 0x3976 [C]     370      13326.3652         0.0002  TORQUE? (GE_f32=13326.4 ft-lbs, varies) | TORQUE_INT? or TARGET? (int16=14710)
  R5235     18000   18000 0x4650 [S]       0         -0.0000     13346.4219  RPM_SETPOINT? (GE_f32=-0.0, constant) | TORQUE_INT? or TARGET? (int16=18000)
  R5236     35248  -30288 0x89B0 [C]   10685          0.0000        -0.0000  RPM? (GE_f32=0.0, varies) | ENCODER/COUNTER? (range=10685)
  R5238     18000   18000 0x4650 [S]       0          0.0000     13312.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=18000)
  R5240        24      24 0x0018 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=24)
  R5241        24      24 0x0018 [S]       0     131072.3750         0.0000  RPM_INT? or COUNT? (int16=24)
  R5242     18432   18432 0x4800 [S]       0          0.0000    131072.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=18432)
  R5244     18000   18000 0x4650 [S]       0          0.0000     13312.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=18000)
  R5247        24      24 0x0018 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=24)
  R5248      2048    2048 0x0800 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=2048)
  R5249         7       7 0x0007 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=7)
  R5250         7       7 0x0007 [S]       0     131072.1094         0.0000  STATE/MODE? (int16=7) | RPM_INT? or COUNT? (int16=7)
  R5251     18432   18432 0x4800 [S]       0          0.0000    131072.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=18432)
  R5253     18000   18000 0x4650 [S]       0          0.0000     13312.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=18000)
  R5256         2       2 0x0002 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=2)
  R5257      2048    2048 0x0800 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=2048)
  R5259        24      24 0x0018 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=24)
  R5260      2048    2048 0x0800 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=2048)
  R5261        30      30 0x001E [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=30)
  R5262        30      30 0x001E [S]       0     131072.4688         0.0000  RPM_INT? or COUNT? (int16=30)
  R5263     18432   18432 0x4800 [S]       0          0.0000    131072.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=18432)
  R5265     18000   18000 0x4650 [S]       0          0.0000     13312.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=18000)
  R5268         2       2 0x0002 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=2)
  R5269      2048    2048 0x0800 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=2048)
  R5271        24      24 0x0018 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=24)
  R5272      2048    2048 0x0800 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=2048)
  R5273       120     120 0x0078 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=120)
  R5274       120     120 0x0078 [S]       0     131073.8750         0.0000  RPM_INT? or COUNT? (int16=120) | TEMP_INT? (int16=120)
  R5275     18432   18432 0x4800 [S]       0          0.0003    131302.1094  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=18432)
  R5276     14727   14727 0x3987 [C]     370      13326.3818         0.0003  TORQUE? (GE_f32=13326.4 ft-lbs, varies) | TORQUE_INT? or TARGET? (int16=14727)
  R5277     18000   18000 0x4650 [S]       0         -0.0000     13344.7666  RPM_SETPOINT? (GE_f32=-0.0, constant) | TORQUE_INT? or TARGET? (int16=18000)
  R5278     33553  -31983 0x8311 [C]   10685          0.0000        -0.0000  RPM? (GE_f32=0.0, varies) | ENCODER/COUNTER? (range=10685)
  R5279         1       1 0x0001 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=1)
  R5280         2       2 0x0002 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=2)
  R5281      2048    2048 0x0800 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=2048)
  R5282         2       2 0x0002 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=2)
  R5283        24      24 0x0018 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=24)
  R5284      2048    2048 0x0800 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=2048)
  R5285       302     302 0x012E [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TEMP_INT? (int16=302)
  R5286       500     500 0x01F4 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant)
  R5287      2048    2048 0x0800 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=2048)
  R5499      3411    3411 0x0D53 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=3411)
  R5501       302     302 0x012E [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TEMP_INT? (int16=302)
  R5505       140     140 0x008C [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=140)
  R5506       124     124 0x007C [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | RPM_INT? or COUNT? (int16=124)
  R5519     16956   16956 0x423C [C]       8         -0.0000        47.1534  RPM? (GE_f32=-0.0, varies) | TORQUE_INT? or TARGET? (int16=16956)
  R5520     40203  -25333 0x9D0B [C]   30364       9639.2607        -0.0000  TORQUE? (GE_f32=9639.3 ft-lbs, varies) | ENCODER/COUNTER? (range=30364)
  R5521     17942   17942 0x4616 [C]      25             ---      9663.9609  TORQUE_INT? or TARGET? (int16=17942)
  R5522     65496     -40 0xFFD8 [S]       0          0.0000            ---  RPM_SETPOINT? (GE_f32=0.0, constant) | CONFIG/FW? (uint16=65496, constant)
  R5528     32000   32000 0x7D00 [S]       0             ---            ---  TORQUE_INT? or TARGET? (int16=32000) | CONFIG/FW? (uint16=32000, constant)
  R5529     28804   28804 0x7084 [C]   48901         46.6099            ---  RPM? (GE_f32=46.6, varies) | TORQUE_INT? or TARGET? (int16=28804)
  R5530     16954   16954 0x423A [C]       7          0.0000        46.5000  RPM? (GE_f32=0.0, varies) | TORQUE_INT? or TARGET? (int16=16954)
  R5545         1       1 0x0001 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=1)
  R5549         3       3 0x0003 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=3)
  R5550         2       2 0x0002 [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | STATE/MODE? (int16=2)
  R5551      2026    2026 0x07EA [S]       0          0.0000         0.0000  RPM_SETPOINT? (GE_f32=0.0, constant) | TORQUE_INT? or TARGET? (int16=2026)

================================================================================
  SUMMARY — LIKELY REGISTER ASSIGNMENTS
================================================================================

  TORQUE:
    R140     raw=  57344  int16=  -8192        GE_f32=1055.00  [VARIES]
    R163     raw=  49152  int16= -16384       GE_f32=18144.00  [VARIES]
    R189     raw=  24576  int16=  24576       GE_f32=19376.00  [VARIES]
    R262     raw=   3619  int16=   3619       GE_f32=29575.07  [VARIES]
    R266     raw=   6189  int16=   6189        GE_f32=9414.04  [VARIES]
    R270     raw=  23010  int16=  23010        GE_f32=7531.24  [VARIES]
    R433     raw=  61440  int16=  -4096       GE_f32=19832.00  [VARIES]
    R502     raw=  52782  int16= -12754        GE_f32=8089.77  [VARIES]
    R607     raw=  42916  int16= -22620        GE_f32=8233.91  [VARIES]
    R5234    raw=  14710  int16=  14710       GE_f32=13326.37  [VARIES]
    R5276    raw=  14727  int16=  14727       GE_f32=13326.38  [VARIES]
    R5520    raw=  40203  int16= -25333        GE_f32=9639.26  [VARIES]

  TORQUE_SETPOINT:
    R161     raw=  21460  int16=  21460       GE_f32=28713.91  [const]
    R308     raw=  58347  int16=  -7189       GE_f32=31729.96  [const]
    R498     raw=  43008  int16= -22528       GE_f32=21460.00  [const]
    R523     raw=  16384  int16=  16384        GE_f32=1250.00  [const]
    R525     raw=  16384  int16=  16384        GE_f32=1250.00  [const]
    R632     raw=  49152  int16= -16384       GE_f32=28000.00  [const]
    R642     raw=  40960  int16= -24576       GE_f32=18000.00  [const]
    R897     raw=  65411  int16=   -125         GE_f32=619.99  [const]
    R1271    raw=  40960  int16= -24576       GE_f32=18000.00  [const]
    R3400    raw=  28160  int16=  28160       GE_f32=21175.00  [const]
    R3402    raw=  22528  int16=  22528       GE_f32=16940.00  [const]
    R3403    raw=  18052  int16=  18052         GE_f32=617.10  [const]
    R3408    raw=  13312  int16=  13312       GE_f32=17434.00  [const]
    R3410    raw=  13312  int16=  13312       GE_f32=17434.00  [const]
    R3412    raw=  56320  int16=  -9216       GE_f32=15351.00  [const]
    R3414    raw=  57241  int16=  -8295       GE_f32=13815.90  [const]
    R3420    raw=   8192  int16=   8192       GE_f32=13000.00  [const]
    R3422    raw=   8192  int16=   8192       GE_f32=13000.00  [const]
    R3427    raw=  28672  int16=  28672       GE_f32=12700.00  [const]
    R3437    raw=  49152  int16= -16384       GE_f32=10160.00  [const]
    R3441    raw=  28672  int16=  28672       GE_f32=19128.00  [const]
    R3479    raw=  20480  int16=  20480        GE_f32=3013.00  [const]
    R3552    raw=   6656  int16=   6656       GE_f32=22157.00  [const]
    R3560    raw=  20480  int16=  20480       GE_f32=25000.00  [const]

  TORQUE_INT:
    R14      raw=   7500  int16=   7500           GE_f32=0.00  [const]
    R130     raw=  20682  int16=  20682          GE_f32=22.79  [const]
    R131     raw=  16822  int16=  16822           GE_f32=0.00  [const]
    R141     raw=  17539  int16=  17539           GE_f32=0.00  [VARIES]
    R145     raw=  12700  int16=  12700                        [const]
    R146     raw=  21460  int16=  21460           GE_f32=0.00  [const]
    R154     raw=  32767  int16=  32767           GE_f32=0.00  [const]
    R155     raw=   1000  int16=   1000                        [const]
    R158     raw=   7360  int16=   7360           GE_f32=0.00  [VARIES]
    R159     raw=  14720  int16=  14720           GE_f32=0.00  [VARIES]
    R161     raw=  21460  int16=  21460       GE_f32=28713.91  [const]
    R162     raw=  18144  int16=  18144          GE_f32=-2.00  [VARIES]
    R164     raw=  18061  int16=  18061           GE_f32=0.00  [const]
    R165     raw=   9961  int16=   9961           GE_f32=0.57  [VARIES]
    R166     raw=  16145  int16=  16145                        [VARIES]
    R167     raw=  28180  int16=  28180         GE_f32=164.43  [VARIES]
    R168     raw=  17188  int16=  17188                        [const]
    R170     raw=  17144  int16=  17144           GE_f32=0.00  [const]
    R178     raw=  17297  int16=  17297           GE_f32=0.00  [const]
    R180     raw=  16928  int16=  16928           GE_f32=0.00  [const]
    R187     raw=   6621  int16=   6621           GE_f32=0.00  [VARIES]
    R189     raw=  24576  int16=  24576       GE_f32=19376.00  [VARIES]
    R190     raw=  18071  int16=  18071           GE_f32=0.00  [VARIES]
    R192     raw=  17747  int16=  17747           GE_f32=0.00  [VARIES]
    R195     raw=   4194  int16=   4194           GE_f32=0.11  [VARIES]
    R196     raw=  15832  int16=  15832                        [VARIES]
    R208     raw=   1012  int16=   1012           GE_f32=0.00  [const]
    R226     raw=  21460  int16=  21460           GE_f32=0.00  [const]
    R228     raw=  12024  int16=  12024           GE_f32=0.00  [const]
    R257     raw=  18595  int16=  18595                        [VARIES]
    R258     raw=  19098  int16=  19098      GE_f32=709801.62  [VARIES]
    R259     raw=  18733  int16=  18733                        [VARIES]
    R260     raw=  23608  int16=  23608      GE_f32=771523.50  [VARIES]
    R261     raw=  18748  int16=  18748           GE_f32=0.00  [VARIES]
    R262     raw=   3619  int16=   3619       GE_f32=29575.07  [VARIES]
    R263     raw=  18151  int16=  18151                        [VARIES]
    R264     raw=  20888  int16=  20888           GE_f32=0.00  [const]
    R266     raw=   6189  int16=   6189        GE_f32=9414.04  [VARIES]
    R267     raw=  17939  int16=  17939           GE_f32=0.00  [VARIES]
    R270     raw=  23010  int16=  23010        GE_f32=7531.24  [VARIES]
    R271     raw=  17899  int16=  17899           GE_f32=0.00  [VARIES]
    R293     raw=  31730  int16=  31730           GE_f32=0.00  [const]
    R303     raw=  32767  int16=  32767           GE_f32=0.00  [const]
    R306     raw=   4701  int16=   4701           GE_f32=1.59  [const]
    R307     raw=  16331  int16=  16331                        [const]
    R309     raw=  18167  int16=  18167           GE_f32=0.00  [const]
    R370     raw=  18022  int16=  18022           GE_f32=0.00  [VARIES]
    R387     raw=  32767  int16=  32767           GE_f32=0.00  [const]
    R388     raw=   3000  int16=   3000                        [const]
    R392     raw=  17633  int16=  17633           GE_f32=0.00  [const]
    R396     raw=  32767  int16=  32767           GE_f32=0.00  [const]
    R397     raw=   3000  int16=   3000                        [const]
    R418     raw=  17312  int16=  17312           GE_f32=0.00  [const]
    R431     raw=  16952  int16=  16952                        [VARIES]
    R432     raw=  19832  int16=  19832                        [VARIES]
    R434     raw=  18074  int16=  18074          GE_f32=-0.00  [const]
    R436     raw=  16158  int16=  16158          GE_f32=-0.00  [const]
    R438     raw=  17203  int16=  17203          GE_f32=-0.00  [const]
    R440     raw=  17163  int16=  17163           GE_f32=0.00  [const]
    R478     raw=   5000  int16=   5000           GE_f32=0.00  [const]
    R483     raw=  32767  int16=  32767           GE_f32=0.00  [const]
    R499     raw=  18087  int16=  18087           GE_f32=0.00  [const]
    R503     raw=  17916  int16=  17916          GE_f32=-0.00  [VARIES]
    R505     raw=  17888  int16=  17888           GE_f32=0.00  [VARIES]
    R506     raw=   7184  int16=   7184                        [VARIES]
    R507     raw=  28597  int16=  28597           GE_f32=0.25  [VARIES]
    R508     raw=  16001  int16=  16001           GE_f32=0.00  [VARIES]
    R512     raw=  15973  int16=  15973           GE_f32=0.00  [VARIES]
    R517     raw=   3000  int16=   3000           GE_f32=0.00  [const]
    R520     raw=  17530  int16=  17530           GE_f32=0.00  [const]
    R522     raw=  17633  int16=  17633           GE_f32=2.00  [const]
    R523     raw=  16384  int16=  16384        GE_f32=1250.00  [const]
    R524     raw=  17564  int16=  17564           GE_f32=2.00  [const]
    R525     raw=  16384  int16=  16384        GE_f32=1250.00  [const]
    R526     raw=  17564  int16=  17564           GE_f32=0.00  [const]
    R528     raw=  17633  int16=  17633           GE_f32=0.00  [const]
    R530     raw=  17549  int16=  17549           GE_f32=0.00  [const]
    R532     raw=  17633  int16=  17633           GE_f32=0.00  [const]
    R608     raw=  17920  int16=  17920           GE_f32=0.00  [VARIES]
    R633     raw=  18138  int16=  18138           GE_f32=0.00  [const]
    R643     raw=  18060  int16=  18060           GE_f32=0.00  [const]
    R644     raw=   3500  int16=   3500           GE_f32=0.00  [const]
    R655     raw=   3500  int16=   3500           GE_f32=0.00  [const]
    R656     raw=   1000  int16=   1000           GE_f32=0.00  [const]
    R863     raw=  16822  int16=  16822           GE_f32=0.00  [const]
    R867     raw=  16202  int16=  16202           GE_f32=0.00  [const]
    R885     raw=   1503  int16=   1503           GE_f32=0.00  [const]
    R898     raw=  17434  int16=  17434           GE_f32=0.79  [const]
    R899     raw=  16202  int16=  16202           GE_f32=0.00  [const]
    R925     raw=   3000  int16=   3000           GE_f32=0.00  [const]
    R931     raw=  32767  int16=  32767           GE_f32=0.00  [const]
    R1239    raw=   6144  int16=   6144           GE_f32=0.00  [const]
    R1272    raw=  18060  int16=  18060           GE_f32=0.00  [const]
    R1371    raw=  20682  int16=  20682          GE_f32=22.79  [const]
    R1372    raw=  16822  int16=  16822           GE_f32=0.00  [const]
    R2000    raw=  20968  int16=  20968           GE_f32=0.00  [const]
    R2002    raw=   4694  int16=   4694                        [const]
    R2003    raw=  20968  int16=  20968           GE_f32=0.00  [const]
    R2004    raw=   2348  int16=   2348                        [const]
    R2005    raw=  20968  int16=  20968           GE_f32=0.00  [const]
    R2007    raw=  21192  int16=  21192           GE_f32=0.00  [const]
    R2009    raw=  20968  int16=  20968                        [const]
    R2012    raw=   5850  int16=   5850           GE_f32=0.00  [const]
    R2031    raw=   6000  int16=   6000           GE_f32=0.00  [const]
    R2033    raw=  13700  int16=  13700           GE_f32=0.00  [const]
    R2034    raw=  15151  int16=  15151                        [const]
    R2035    raw=  32000  int16=  32000           GE_f32=0.00  [const]
    R2036    raw=  12700  int16=  12700           GE_f32=0.00  [const]
    R2037    raw=   1806  int16=   1806           GE_f32=0.01  [const]
    R2038    raw=  15451  int16=  15451           GE_f32=0.00  [const]
    R2039    raw=  13420  int16=  13420           GE_f32=0.01  [const]
    R2040    raw=  15351  int16=  15351           GE_f32=0.00  [const]
    R2042    raw=  20682  int16=  20682           GE_f32=0.00  [const]
    R2047    raw=  15451  int16=  15451           GE_f32=0.00  [const]
    R2051    raw=  32000  int16=  32000           GE_f32=0.00  [const]
    R2058    raw=   1000  int16=   1000           GE_f32=0.00  [const]
    R2060    raw=   1000  int16=   1000           GE_f32=0.00  [const]
    R2062    raw=   5000  int16=   5000           GE_f32=0.00  [const]
    R2070    raw=  29300  int16=  29300           GE_f32=0.00  [VARIES]
    R2073    raw=  29792  int16=  29792           GE_f32=0.00  [VARIES]
    R2074    raw=   4478  int16=   4478           GE_f32=0.00  [VARIES]
    R2076    raw=   6223  int16=   6223           GE_f32=0.00  [VARIES]
    R2078    raw=   4434  int16=   4434           GE_f32=0.00  [VARIES]
    R2100    raw=    800  int16=    800           GE_f32=0.00  [const]
    R2102    raw=   2000  int16=   2000           GE_f32=0.00  [const]
    R2104    raw=  16822  int16=  16822           GE_f32=0.00  [const]
    R2105    raw=  13000  int16=  13000           GE_f32=0.00  [const]
    R2108    raw=  13000  int16=  13000           GE_f32=0.00  [const]
    R2111    raw=  13000  int16=  13000                        [const]
    R2113    raw=  13000  int16=  13000           GE_f32=0.00  [const]
    R2115    raw=   8768  int16=   8768           GE_f32=0.00  [const]
    R2117    raw=  13000  int16=  13000                        [const]
    R2120    raw=   8932  int16=   8932           GE_f32=0.00  [const]
    R2141    raw=    891  int16=    891           GE_f32=0.00  [const]
    R2145    raw=  13400  int16=  13400           GE_f32=0.00  [const]
    R2149    raw=  12600  int16=  12600                        [const]
    R2150    raw=  32000  int16=  32000           GE_f32=0.00  [const]
    R2154    raw=    514  int16=    514           GE_f32=0.00  [const]
    R2194    raw=  17588  int16=  17588           GE_f32=0.00  [const]
    R2196    raw=  17588  int16=  17588           GE_f32=0.00  [const]
    R3400    raw=  28160  int16=  28160       GE_f32=21175.00  [const]
    R3401    raw=  18085  int16=  18085                        [const]
    R3402    raw=  22528  int16=  22528       GE_f32=16940.00  [const]
    R3403    raw=  18052  int16=  18052         GE_f32=617.10  [const]
    R3404    raw=  17434  int16=  17434           GE_f32=0.00  [const]
    R3405    raw=  13816  int16=  13816           GE_f32=0.00  [const]
    R3407    raw=  13000  int16=  13000           GE_f32=0.00  [const]
    R3408    raw=  13312  int16=  13312       GE_f32=17434.00  [const]
    R3409    raw=  18056  int16=  18056           GE_f32=0.00  [const]
    R3410    raw=  13312  int16=  13312       GE_f32=17434.00  [const]
    R3411    raw=  18056  int16=  18056                        [const]
    R3413    raw=  18031  int16=  18031                        [const]
    R3415    raw=  18007  int16=  18007           GE_f32=0.00  [const]
    R3420    raw=   8192  int16=   8192       GE_f32=13000.00  [const]
    R3421    raw=  17995  int16=  17995           GE_f32=0.00  [const]
    R3422    raw=   8192  int16=   8192       GE_f32=13000.00  [const]
    R3423    raw=  17995  int16=  17995           GE_f32=0.00  [const]
    R3427    raw=  28672  int16=  28672       GE_f32=12700.00  [const]
    R3428    raw=  17990  int16=  17990           GE_f32=0.00  [const]
    R3438    raw=  17950  int16=  17950           GE_f32=0.00  [const]
    R3439    raw=  10160  int16=  10160           GE_f32=0.00  [const]
    R3441    raw=  28672  int16=  28672       GE_f32=19128.00  [const]
    R3442    raw=  18069  int16=  18069           GE_f32=0.00  [const]
    R3446    raw=   2026  int16=   2026           GE_f32=0.00  [const]
    R3454    raw=   1635  int16=   1635           GE_f32=0.00  [const]
    R3468    raw=  18434  int16=  18434           GE_f32=0.00  [const]
    R3469    raw=  13608  int16=  13608           GE_f32=0.00  [const]
    R3472    raw=   1635  int16=   1635                        [const]
    R3473    raw=  32767  int16=  32767           GE_f32=0.00  [const]
    R3479    raw=  20480  int16=  20480        GE_f32=3013.00  [const]
    R3480    raw=  17724  int16=  17724           GE_f32=0.00  [const]
    R3496    raw=   1000  int16=   1000           GE_f32=0.00  [const]
    R3500    raw=   9845  int16=   9845          GE_f32=22.77  [const]
    R3501    raw=  16822  int16=  16822           GE_f32=0.00  [const]
    R3516    raw=    517  int16=    517           GE_f32=0.00  [const]
    R3531    raw=  16822  int16=  16822          GE_f32=17.91  [const]
    R3532    raw=  16783  int16=  16783          GE_f32=17.91  [const]
    R3533    raw=  16783  int16=  16783          GE_f32=22.78  [const]
    R3534    raw=  16822  int16=  16822           GE_f32=0.00  [const]
    R3535    raw=  13000  int16=  13000           GE_f32=0.34  [const]
    R3536    raw=  16046  int16=  16046      GE_f32=483829.44  [const]
    R3537    raw=  18668  int16=  18668                        [const]
    R3538    raw=  19062  int16=  19062           GE_f32=0.01  [const]
    R3539    raw=  15443  int16=  15443          GE_f32=24.15  [VARIES]
    R3540    raw=  16833  int16=  16833           GE_f32=0.00  [VARIES]
    R3542    raw=  20968  int16=  20968           GE_f32=0.00  [const]
    R3543    raw=   4251  int16=   4251           GE_f32=0.00  [const]
    R3545    raw=  26624  int16=  26624           GE_f32=0.00  [const]
    R3548    raw=   6144  int16=   6144           GE_f32=0.01  [const]
    R3549    raw=  15351  int16=  15351           GE_f32=0.00  [const]
    R3550    raw=  12700  int16=  12700           GE_f32=0.00  [const]
    R3552    raw=   6656  int16=   6656       GE_f32=22157.00  [const]
    R3553    raw=  18093  int16=  18093                        [const]
    R3554    raw=  21155  int16=  21155           GE_f32=0.00  [const]
    R3558    raw=  28804  int16=  28804          GE_f32=46.61  [VARIES]
    R3559    raw=  16954  int16=  16954                        [VARIES]
    R3560    raw=  20480  int16=  20480       GE_f32=25000.00  [const]
    R3561    raw=  18115  int16=  18115           GE_f32=0.00  [const]
    R3584    raw=  30481  int16=  30481                        [VARIES]
    R3589    raw=  30481  int16=  30481           GE_f32=0.00  [VARIES]
    R3590    raw=   8711  int16=   8711                        [VARIES]
    R3591    raw=  30481  int16=  30481           GE_f32=0.00  [VARIES]
    R5106    raw=   2413  int16=   2413           GE_f32=0.00  [const]
    R5232    raw=   2048  int16=   2048           GE_f32=0.00  [const]
    R5234    raw=  14710  int16=  14710       GE_f32=13326.37  [VARIES]
    R5235    raw=  18000  int16=  18000          GE_f32=-0.00  [const]
    R5238    raw=  18000  int16=  18000           GE_f32=0.00  [const]
    R5242    raw=  18432  int16=  18432           GE_f32=0.00  [const]
    R5244    raw=  18000  int16=  18000           GE_f32=0.00  [const]
    R5248    raw=   2048  int16=   2048           GE_f32=0.00  [const]
    R5251    raw=  18432  int16=  18432           GE_f32=0.00  [const]
    R5253    raw=  18000  int16=  18000           GE_f32=0.00  [const]
    R5257    raw=   2048  int16=   2048           GE_f32=0.00  [const]
    R5260    raw=   2048  int16=   2048           GE_f32=0.00  [const]
    R5263    raw=  18432  int16=  18432           GE_f32=0.00  [const]
    R5265    raw=  18000  int16=  18000           GE_f32=0.00  [const]
    R5269    raw=   2048  int16=   2048           GE_f32=0.00  [const]
    R5272    raw=   2048  int16=   2048           GE_f32=0.00  [const]
    R5275    raw=  18432  int16=  18432           GE_f32=0.00  [const]
    R5276    raw=  14727  int16=  14727       GE_f32=13326.38  [VARIES]
    R5277    raw=  18000  int16=  18000          GE_f32=-0.00  [const]
    R5281    raw=   2048  int16=   2048           GE_f32=0.00  [const]
    R5284    raw=   2048  int16=   2048           GE_f32=0.00  [const]
    R5287    raw=   2048  int16=   2048           GE_f32=0.00  [const]
    R5499    raw=   3411  int16=   3411           GE_f32=0.00  [const]
    R5519    raw=  16956  int16=  16956          GE_f32=-0.00  [VARIES]
    R5521    raw=  17942  int16=  17942                        [VARIES]
    R5528    raw=  32000  int16=  32000                        [const]
    R5529    raw=  28804  int16=  28804          GE_f32=46.61  [VARIES]
    R5530    raw=  16954  int16=  16954           GE_f32=0.00  [VARIES]
    R5551    raw=   2026  int16=   2026           GE_f32=0.00  [const]

  RPM:
    R141     raw=  17539  int16=  17539           GE_f32=0.00  [VARIES]
    R157     raw=     46  int16=     46           GE_f32=0.00  [VARIES]
    R158     raw=   7360  int16=   7360           GE_f32=0.00  [VARIES]
    R159     raw=  14720  int16=  14720           GE_f32=0.00  [VARIES]
    R162     raw=  18144  int16=  18144          GE_f32=-2.00  [VARIES]
    R165     raw=   9961  int16=   9961           GE_f32=0.57  [VARIES]
    R167     raw=  28180  int16=  28180         GE_f32=164.43  [VARIES]
    R169     raw=  56360  int16=  -9176         GE_f32=124.43  [VARIES]
    R181     raw=     46  int16=     46           GE_f32=0.00  [VARIES]
    R187     raw=   6621  int16=   6621           GE_f32=0.00  [VARIES]
    R190     raw=  18071  int16=  18071           GE_f32=0.00  [VARIES]
    R192     raw=  17747  int16=  17747           GE_f32=0.00  [VARIES]
    R195     raw=   4194  int16=   4194           GE_f32=0.11  [VARIES]
    R261     raw=  18748  int16=  18748           GE_f32=0.00  [VARIES]
    R267     raw=  17939  int16=  17939           GE_f32=0.00  [VARIES]
    R271     raw=  17899  int16=  17899           GE_f32=0.00  [VARIES]
    R370     raw=  18022  int16=  18022           GE_f32=0.00  [VARIES]
    R435     raw=  42992  int16= -22544           GE_f32=0.62  [VARIES]
    R437     raw=  47678  int16= -17858         GE_f32=179.73  [VARIES]
    R439     raw=  47678  int16= -17858         GE_f32=139.73  [VARIES]
    R503     raw=  17916  int16=  17916          GE_f32=-0.00  [VARIES]
    R505     raw=  17888  int16=  17888           GE_f32=0.00  [VARIES]
    R507     raw=  28597  int16=  28597           GE_f32=0.25  [VARIES]
    R508     raw=  16001  int16=  16001           GE_f32=0.00  [VARIES]
    R511     raw=  58196  int16=  -7340           GE_f32=0.22  [VARIES]
    R512     raw=  15973  int16=  15973           GE_f32=0.00  [VARIES]
    R608     raw=  17920  int16=  17920           GE_f32=0.00  [VARIES]
    R2070    raw=  29300  int16=  29300           GE_f32=0.00  [VARIES]
    R2073    raw=  29792  int16=  29792           GE_f32=0.00  [VARIES]
    R2074    raw=   4478  int16=   4478           GE_f32=0.00  [VARIES]
    R2076    raw=   6223  int16=   6223           GE_f32=0.00  [VARIES]
    R2078    raw=   4434  int16=   4434           GE_f32=0.00  [VARIES]
    R3430    raw=     47  int16=     47           GE_f32=0.00  [VARIES]
    R3539    raw=  15443  int16=  15443          GE_f32=24.15  [VARIES]
    R3540    raw=  16833  int16=  16833           GE_f32=0.00  [VARIES]
    R3558    raw=  28804  int16=  28804          GE_f32=46.61  [VARIES]
    R3589    raw=  30481  int16=  30481           GE_f32=0.00  [VARIES]
    R3591    raw=  30481  int16=  30481           GE_f32=0.00  [VARIES]
    R5236    raw=  35248  int16= -30288           GE_f32=0.00  [VARIES]
    R5278    raw=  33553  int16= -31983           GE_f32=0.00  [VARIES]
    R5519    raw=  16956  int16=  16956          GE_f32=-0.00  [VARIES]
    R5529    raw=  28804  int16=  28804          GE_f32=46.61  [VARIES]
    R5530    raw=  16954  int16=  16954           GE_f32=0.00  [VARIES]

  RPM_SETPOINT:
    R0       raw=      4  int16=      4           GE_f32=0.00  [const]
    R2       raw=      8  int16=      8           GE_f32=0.00  [const]
    R3       raw=     13  int16=     13           GE_f32=0.00  [const]
    R6       raw=  57857  int16=  -7679           GE_f32=0.00  [const]
    R7       raw=      6  int16=      6           GE_f32=0.00  [const]
    R8       raw=      8  int16=      8           GE_f32=0.00  [const]
    R9       raw=     10  int16=     10           GE_f32=0.00  [const]
    R10      raw=    257  int16=    257           GE_f32=0.00  [const]
    R13      raw=      1  int16=      1           GE_f32=0.00  [const]
    R14      raw=   7500  int16=   7500           GE_f32=0.00  [const]
    R18      raw=      2  int16=      2           GE_f32=0.00  [const]
    R31      raw=     25  int16=     25           GE_f32=0.00  [const]
    R34      raw=     25  int16=     25           GE_f32=0.00  [const]
    R37      raw=     25  int16=     25           GE_f32=0.00  [const]
    R40      raw=     25  int16=     25           GE_f32=0.00  [const]
    R43      raw=     25  int16=     25           GE_f32=0.00  [const]
    R46      raw=     25  int16=     25           GE_f32=0.00  [const]
    R49      raw=     25  int16=     25           GE_f32=0.00  [const]
    R52      raw=     25  int16=     25           GE_f32=0.00  [const]
    R55      raw=     25  int16=     25           GE_f32=0.00  [const]
    R61      raw=     25  int16=     25           GE_f32=0.00  [const]
    R64      raw=     25  int16=     25           GE_f32=0.00  [const]
    R67      raw=     25  int16=     25           GE_f32=0.00  [const]
    R70      raw=     25  int16=     25           GE_f32=0.00  [const]
    R73      raw=     25  int16=     25           GE_f32=0.00  [const]
    R76      raw=     25  int16=     25           GE_f32=0.00  [const]
    R79      raw=     25  int16=     25           GE_f32=0.00  [const]
    R82      raw=     25  int16=     25           GE_f32=0.00  [const]
    R85      raw=     25  int16=     25           GE_f32=0.00  [const]
    R88      raw=     25  int16=     25           GE_f32=0.00  [const]
    R91      raw=     25  int16=     25           GE_f32=0.00  [const]
    R94      raw=     25  int16=     25           GE_f32=0.00  [const]
    R97      raw=     25  int16=     25           GE_f32=0.00  [const]
    R100     raw=     25  int16=     25           GE_f32=0.00  [const]
    R103     raw=     50  int16=     50           GE_f32=0.00  [const]
    R107     raw=      5  int16=      5           GE_f32=0.00  [const]
    R108     raw=     50  int16=     50           GE_f32=0.00  [const]
    R110     raw=     25  int16=     25           GE_f32=0.00  [const]
    R118     raw=     10  int16=     10           GE_f32=0.00  [const]
    R124     raw=    100  int16=    100           GE_f32=0.00  [const]
    R130     raw=  20682  int16=  20682          GE_f32=22.79  [const]
    R131     raw=  16822  int16=  16822           GE_f32=0.00  [const]
    R146     raw=  21460  int16=  21460           GE_f32=0.00  [const]
    R151     raw=      5  int16=      5           GE_f32=0.00  [const]
    R152     raw=      5  int16=      5           GE_f32=0.00  [const]
    R154     raw=  32767  int16=  32767           GE_f32=0.00  [const]
    R156     raw=  52660  int16= -12876           GE_f32=0.00  [const]
    R164     raw=  18061  int16=  18061           GE_f32=0.00  [const]
    R170     raw=  17144  int16=  17144           GE_f32=0.00  [const]
    R173     raw=  65531  int16=     -5           GE_f32=0.00  [const]
    R175     raw=      5  int16=      5           GE_f32=0.00  [const]
    R178     raw=  17297  int16=  17297           GE_f32=0.00  [const]
    R180     raw=  16928  int16=  16928           GE_f32=0.00  [const]
    R185     raw=     20  int16=     20           GE_f32=0.00  [const]
    R197     raw=  65531  int16=     -5           GE_f32=0.00  [const]
    R206     raw=      5  int16=      5           GE_f32=0.00  [const]
    R207     raw=      5  int16=      5           GE_f32=0.00  [const]
    R208     raw=   1012  int16=   1012           GE_f32=0.00  [const]
    R212     raw=     30  int16=     30           GE_f32=0.00  [const]
    R219     raw=      5  int16=      5           GE_f32=0.00  [const]
    R222     raw=      5  int16=      5           GE_f32=0.00  [const]
    R226     raw=  21460  int16=  21460           GE_f32=0.00  [const]
    R228     raw=  12024  int16=  12024           GE_f32=0.00  [const]
    R246     raw=     30  int16=     30           GE_f32=0.00  [const]
    R252     raw=     50  int16=     50           GE_f32=0.00  [const]
    R264     raw=  20888  int16=  20888           GE_f32=0.00  [const]
    R293     raw=  31730  int16=  31730           GE_f32=0.00  [const]
    R297     raw=     80  int16=     80           GE_f32=0.00  [const]
    R298     raw=     80  int16=     80           GE_f32=0.00  [const]
    R299     raw=     95  int16=     95           GE_f32=0.00  [const]
    R301     raw=     80  int16=     80           GE_f32=0.00  [const]
    R303     raw=  32767  int16=  32767           GE_f32=0.00  [const]
    R305     raw=  64776  int16=   -760           GE_f32=0.00  [const]
    R306     raw=   4701  int16=   4701           GE_f32=1.59  [const]
    R309     raw=  18167  int16=  18167           GE_f32=0.00  [const]
    R313     raw=      5  int16=      5           GE_f32=0.00  [const]
    R316     raw=      5  int16=      5           GE_f32=0.00  [const]
    R380     raw=  49696  int16= -15840           GE_f32=0.00  [const]
    R382     raw=     20  int16=     20           GE_f32=0.00  [const]
    R385     raw=     20  int16=     20           GE_f32=0.00  [const]
    R387     raw=  32767  int16=  32767           GE_f32=0.00  [const]
    R389     raw=  63921  int16=  -1615           GE_f32=0.00  [const]
    R392     raw=  17633  int16=  17633           GE_f32=0.00  [const]
    R394     raw=     20  int16=     20           GE_f32=0.00  [const]
    R396     raw=  32767  int16=  32767           GE_f32=0.00  [const]
    R398     raw=  63922  int16=  -1614           GE_f32=0.00  [const]
    R400     raw=      3  int16=      3           GE_f32=0.00  [const]
    R418     raw=  17312  int16=  17312           GE_f32=0.00  [const]
    R434     raw=  18074  int16=  18074          GE_f32=-0.00  [const]
    R436     raw=  16158  int16=  16158          GE_f32=-0.00  [const]
    R438     raw=  17203  int16=  17203          GE_f32=-0.00  [const]
    R440     raw=  17163  int16=  17163           GE_f32=0.00  [const]
    R442     raw=      5  int16=      5           GE_f32=0.00  [const]
    R478     raw=   5000  int16=   5000           GE_f32=0.00  [const]
    R483     raw=  32767  int16=  32767           GE_f32=0.00  [const]
    R485     raw=  63921  int16=  -1615           GE_f32=0.00  [const]
    R499     raw=  18087  int16=  18087           GE_f32=0.00  [const]
    R517     raw=   3000  int16=   3000           GE_f32=0.00  [const]
    R520     raw=  17530  int16=  17530           GE_f32=0.00  [const]
    R522     raw=  17633  int16=  17633           GE_f32=2.00  [const]
    R524     raw=  17564  int16=  17564           GE_f32=2.00  [const]
    R526     raw=  17564  int16=  17564           GE_f32=0.00  [const]
    R528     raw=  17633  int16=  17633           GE_f32=0.00  [const]
    R530     raw=  17549  int16=  17549           GE_f32=0.00  [const]
    R532     raw=  17633  int16=  17633           GE_f32=0.00  [const]
    R534     raw=     50  int16=     50           GE_f32=0.00  [const]
    R537     raw=    500  int16=    500           GE_f32=0.00  [const]
    R540     raw=     10  int16=     10           GE_f32=0.00  [const]
    R543     raw=     10  int16=     10           GE_f32=0.00  [const]
    R546     raw=     80  int16=     80           GE_f32=0.00  [const]
    R549     raw=     80  int16=     80           GE_f32=0.00  [const]
    R552     raw=     80  int16=     80           GE_f32=0.00  [const]
    R555     raw=     80  int16=     80           GE_f32=0.00  [const]
    R558     raw=     80  int16=     80           GE_f32=0.00  [const]
    R561     raw=     80  int16=     80           GE_f32=0.00  [const]
    R564     raw=     80  int16=     80           GE_f32=0.00  [const]
    R567     raw=     80  int16=     80           GE_f32=0.00  [const]
    R570     raw=     80  int16=     80           GE_f32=0.00  [const]
    R573     raw=     80  int16=     80           GE_f32=0.00  [const]
    R576     raw=     80  int16=     80           GE_f32=0.00  [const]
    R579     raw=     80  int16=     80           GE_f32=0.00  [const]
    R582     raw=     80  int16=     80           GE_f32=0.00  [const]
    R585     raw=     80  int16=     80           GE_f32=0.00  [const]
    R588     raw=     80  int16=     80           GE_f32=0.00  [const]
    R591     raw=     80  int16=     80           GE_f32=0.00  [const]
    R594     raw=     80  int16=     80           GE_f32=0.00  [const]
    R597     raw=     80  int16=     80           GE_f32=0.00  [const]
    R599     raw=     15  int16=     15           GE_f32=0.00  [const]
    R610     raw=     80  int16=     80           GE_f32=0.00  [const]
    R613     raw=     80  int16=     80           GE_f32=0.00  [const]
    R616     raw=     80  int16=     80           GE_f32=0.00  [const]
    R619     raw=     80  int16=     80           GE_f32=0.00  [const]
    R622     raw=     80  int16=     80           GE_f32=0.00  [const]
    R625     raw=     80  int16=     80           GE_f32=0.00  [const]
    R628     raw=     80  int16=     80           GE_f32=0.00  [const]
    R633     raw=  18138  int16=  18138           GE_f32=0.00  [const]
    R643     raw=  18060  int16=  18060           GE_f32=0.00  [const]
    R644     raw=   3500  int16=   3500           GE_f32=0.00  [const]
    R646     raw=     80  int16=     80           GE_f32=0.00  [const]
    R649     raw=     80  int16=     80           GE_f32=0.00  [const]
    R655     raw=   3500  int16=   3500           GE_f32=0.00  [const]
    R656     raw=   1000  int16=   1000           GE_f32=0.00  [const]
    R659     raw=  65486  int16=    -50           GE_f32=0.00  [const]
    R677     raw=    100  int16=    100           GE_f32=0.00  [const]
    R679     raw=     50  int16=     50           GE_f32=0.00  [const]
    R682     raw=     50  int16=     50           GE_f32=0.00  [const]
    R685     raw=     30  int16=     30           GE_f32=0.00  [const]
    R688     raw=     30  int16=     30           GE_f32=0.00  [const]
    R691     raw=     10  int16=     10           GE_f32=0.00  [const]
    R693     raw=      3  int16=      3           GE_f32=0.00  [const]
    R694     raw=      3  int16=      3           GE_f32=0.00  [const]
    R695     raw=     24  int16=     24           GE_f32=0.00  [const]
    R697     raw=     30  int16=     30           GE_f32=0.00  [const]
    R700     raw=     30  int16=     30           GE_f32=0.00  [const]
    R703     raw=     30  int16=     30           GE_f32=0.00  [const]
    R706     raw=     30  int16=     30           GE_f32=0.00  [const]
    R709     raw=     30  int16=     30           GE_f32=0.00  [const]
    R712     raw=     30  int16=     30           GE_f32=0.00  [const]
    R715     raw=     30  int16=     30           GE_f32=0.00  [const]
    R718     raw=     30  int16=     30           GE_f32=0.00  [const]
    R721     raw=     30  int16=     30           GE_f32=0.00  [const]
    R724     raw=     30  int16=     30           GE_f32=0.00  [const]
    R727     raw=     30  int16=     30           GE_f32=0.00  [const]
    R730     raw=     30  int16=     30           GE_f32=0.00  [const]
    R863     raw=  16822  int16=  16822           GE_f32=0.00  [const]
    R864     raw=     50  int16=     50           GE_f32=0.00  [const]
    R865     raw=     50  int16=     50           GE_f32=0.00  [const]
    R866     raw=    344  int16=    344           GE_f32=0.79  [const]
    R867     raw=  16202  int16=  16202           GE_f32=0.00  [const]
    R883     raw=      2  int16=      2           GE_f32=0.00  [const]
    R884     raw=      2  int16=      2           GE_f32=0.00  [const]
    R885     raw=   1503  int16=   1503           GE_f32=0.00  [const]
    R898     raw=  17434  int16=  17434           GE_f32=0.79  [const]
    R899     raw=  16202  int16=  16202           GE_f32=0.00  [const]
    R925     raw=   3000  int16=   3000           GE_f32=0.00  [const]
    R928     raw=      4  int16=      4           GE_f32=0.00  [const]
    R931     raw=  32767  int16=  32767           GE_f32=0.00  [const]
    R933     raw=  64500  int16=  -1036           GE_f32=0.00  [const]
    R934     raw=      8  int16=      8           GE_f32=0.00  [const]
    R935     raw=      8  int16=      8           GE_f32=0.00  [const]
    R936     raw=    151  int16=    151           GE_f32=0.00  [const]
    R1151    raw=      4  int16=      4           GE_f32=0.00  [const]
    R1153    raw=     30  int16=     30           GE_f32=0.00  [const]
    R1156    raw=  65507  int16=    -29           GE_f32=0.00  [const]
    R1158    raw=  65535  int16=     -1           GE_f32=0.00  [const]
    R1159    raw=     31  int16=     31           GE_f32=0.00  [const]
    R1160    raw=      1  int16=      1           GE_f32=0.00  [const]
    R1162    raw=    100  int16=    100           GE_f32=0.00  [const]
    R1236    raw=      8  int16=      8           GE_f32=0.00  [const]
    R1238    raw=      5  int16=      5           GE_f32=0.00  [const]
    R1239    raw=   6144  int16=   6144           GE_f32=0.00  [const]
    R1241    raw=      2  int16=      2           GE_f32=0.00  [const]
    R1272    raw=  18060  int16=  18060           GE_f32=0.00  [const]
    R1371    raw=  20682  int16=  20682          GE_f32=22.79  [const]
    R1372    raw=  16822  int16=  16822           GE_f32=0.00  [const]
    R1449    raw=     80  int16=     80           GE_f32=0.00  [const]
    R1452    raw=     80  int16=     80           GE_f32=0.00  [const]
    R2000    raw=  20968  int16=  20968           GE_f32=0.00  [const]
    R2003    raw=  20968  int16=  20968           GE_f32=0.00  [const]
    R2005    raw=  20968  int16=  20968           GE_f32=0.00  [const]
    R2007    raw=  21192  int16=  21192           GE_f32=0.00  [const]
    R2010    raw=  62526  int16=  -3010           GE_f32=0.00  [const]
    R2011    raw=     15  int16=     15           GE_f32=0.00  [const]
    R2012    raw=   5850  int16=   5850           GE_f32=0.00  [const]
    R2027    raw=      1  int16=      1           GE_f32=0.00  [const]
    R2028    raw=      1  int16=      1           GE_f32=0.00  [const]
    R2029    raw=     65  int16=     65           GE_f32=0.00  [const]
    R2031    raw=   6000  int16=   6000           GE_f32=0.00  [const]
    R2032    raw=     10  int16=     10           GE_f32=0.00  [const]
    R2033    raw=  13700  int16=  13700           GE_f32=0.00  [const]
    R2035    raw=  32000  int16=  32000           GE_f32=0.00  [const]
    R2036    raw=  12700  int16=  12700           GE_f32=0.00  [const]
    R2037    raw=   1806  int16=   1806           GE_f32=0.01  [const]
    R2038    raw=  15451  int16=  15451           GE_f32=0.00  [const]
    R2039    raw=  13420  int16=  13420           GE_f32=0.01  [const]
    R2040    raw=  15351  int16=  15351           GE_f32=0.00  [const]
    R2042    raw=  20682  int16=  20682           GE_f32=0.00  [const]
    R2046    raw=    108  int16=    108           GE_f32=0.01  [const]
    R2047    raw=  15451  int16=  15451           GE_f32=0.00  [const]
    R2049    raw=    500  int16=    500           GE_f32=0.00  [const]
    R2051    raw=  32000  int16=  32000           GE_f32=0.00  [const]
    R2054    raw=      2  int16=      2           GE_f32=0.00  [const]
    R2057    raw=  65524  int16=    -12           GE_f32=0.00  [const]
    R2058    raw=   1000  int16=   1000           GE_f32=0.00  [const]
    R2060    raw=   1000  int16=   1000           GE_f32=0.00  [const]
    R2062    raw=   5000  int16=   5000           GE_f32=0.00  [const]
    R2063    raw=    500  int16=    500           GE_f32=0.00  [const]
    R2066    raw=    500  int16=    500           GE_f32=0.00  [const]
    R2067    raw=      4  int16=      4           GE_f32=0.00  [const]
    R2071    raw=    500  int16=    500           GE_f32=0.00  [const]
    R2075    raw=    500  int16=    500           GE_f32=0.00  [const]
    R2077    raw=     16  int16=     16           GE_f32=0.00  [const]
    R2096    raw=      2  int16=      2           GE_f32=0.00  [const]
    R2100    raw=    800  int16=    800           GE_f32=0.00  [const]
    R2102    raw=   2000  int16=   2000           GE_f32=0.00  [const]
    R2104    raw=  16822  int16=  16822           GE_f32=0.00  [const]
    R2105    raw=  13000  int16=  13000           GE_f32=0.00  [const]
    R2107    raw=      1  int16=      1           GE_f32=0.00  [const]
    R2108    raw=  13000  int16=  13000           GE_f32=0.00  [const]
    R2110    raw=  60842  int16=  -4694           GE_f32=0.00  [const]
    R2112    raw=  60934  int16=  -4602           GE_f32=0.00  [const]
    R2113    raw=  13000  int16=  13000           GE_f32=0.00  [const]
    R2115    raw=   8768  int16=   8768           GE_f32=0.00  [const]
    R2116    raw=    187  int16=    187           GE_f32=0.00  [const]
    R2118    raw=  62510  int16=  -3026           GE_f32=0.00  [const]
    R2119    raw=     15  int16=     15           GE_f32=0.00  [const]
    R2120    raw=   8932  int16=   8932           GE_f32=0.00  [const]
    R2136    raw=      5  int16=      5          GE_f32=-2.00  [const]
    R2137    raw=  49152  int16= -16384           GE_f32=0.00  [const]
    R2139    raw=     55  int16=     55           GE_f32=0.00  [const]
    R2140    raw=     55  int16=     55           GE_f32=0.00  [const]
    R2141    raw=    891  int16=    891           GE_f32=0.00  [const]
    R2143    raw=      5  int16=      5          GE_f32=-2.00  [const]
    R2144    raw=  49152  int16= -16384           GE_f32=0.00  [const]
    R2145    raw=  13400  int16=  13400           GE_f32=0.00  [const]
    R2147    raw=     50  int16=     50           GE_f32=0.00  [const]
    R2150    raw=  32000  int16=  32000           GE_f32=0.00  [const]
    R2152    raw=     25  int16=     25           GE_f32=0.00  [const]
    R2153    raw=     25  int16=     25           GE_f32=0.00  [const]
    R2154    raw=    514  int16=    514           GE_f32=0.00  [const]
    R2194    raw=  17588  int16=  17588           GE_f32=0.00  [const]
    R2196    raw=  17588  int16=  17588           GE_f32=0.00  [const]
    R2197    raw=      7  int16=      7           GE_f32=0.00  [const]
    R2198    raw=      7  int16=      7           GE_f32=0.00  [const]
    R2199    raw=    102  int16=    102           GE_f32=0.00  [const]
    R3404    raw=  17434  int16=  17434           GE_f32=0.00  [const]
    R3405    raw=  13816  int16=  13816           GE_f32=0.00  [const]
    R3407    raw=  13000  int16=  13000           GE_f32=0.00  [const]
    R3409    raw=  18056  int16=  18056           GE_f32=0.00  [const]
    R3415    raw=  18007  int16=  18007           GE_f32=0.00  [const]
    R3421    raw=  17995  int16=  17995           GE_f32=0.00  [const]
    R3423    raw=  17995  int16=  17995           GE_f32=0.00  [const]
    R3425    raw=      5  int16=      5           GE_f32=0.00  [const]
    R3428    raw=  17990  int16=  17990           GE_f32=0.00  [const]
    R3438    raw=  17950  int16=  17950           GE_f32=0.00  [const]
    R3439    raw=  10160  int16=  10160           GE_f32=0.00  [const]
    R3442    raw=  18069  int16=  18069           GE_f32=0.00  [const]
    R3445    raw=      3  int16=      3           GE_f32=0.00  [const]
    R3446    raw=   2026  int16=   2026           GE_f32=0.00  [const]
    R3448    raw=      2  int16=      2           GE_f32=0.00  [const]
    R3449    raw=      2  int16=      2           GE_f32=0.00  [const]
    R3450    raw=     17  int16=     17           GE_f32=0.00  [const]
    R3451    raw=     44  int16=     44           GE_f32=0.00  [const]
    R3452    raw=      5  int16=      5           GE_f32=0.00  [const]
    R3453    raw=      5  int16=      5           GE_f32=0.00  [const]
    R3454    raw=   1635  int16=   1635           GE_f32=0.00  [const]
    R3457    raw=     10  int16=     10           GE_f32=0.00  [const]
    R3460    raw=     10  int16=     10           GE_f32=0.00  [const]
    R3468    raw=  18434  int16=  18434           GE_f32=0.00  [const]
    R3469    raw=  13608  int16=  13608           GE_f32=0.00  [const]
    R3470    raw=      5  int16=      5           GE_f32=0.00  [const]
    R3471    raw=      5  int16=      5           GE_f32=0.00  [const]
    R3473    raw=  32767  int16=  32767           GE_f32=0.00  [const]
    R3475    raw=  64023  int16=  -1513           GE_f32=0.00  [const]
    R3477    raw=     15  int16=     15           GE_f32=0.00  [const]
    R3480    raw=  17724  int16=  17724           GE_f32=0.00  [const]
    R3495    raw=  65534  int16=     -2           GE_f32=0.00  [const]
    R3496    raw=   1000  int16=   1000           GE_f32=0.00  [const]
    R3500    raw=   9845  int16=   9845          GE_f32=22.77  [const]
    R3501    raw=  16822  int16=  16822           GE_f32=0.00  [const]
    R3514    raw=     21  int16=     21           GE_f32=0.00  [const]
    R3516    raw=    517  int16=    517           GE_f32=0.00  [const]
    R3531    raw=  16822  int16=  16822          GE_f32=17.91  [const]
    R3532    raw=  16783  int16=  16783          GE_f32=17.91  [const]
    R3533    raw=  16783  int16=  16783          GE_f32=22.78  [const]
    R3534    raw=  16822  int16=  16822           GE_f32=0.00  [const]
    R3535    raw=  13000  int16=  13000           GE_f32=0.34  [const]
    R3538    raw=  19062  int16=  19062           GE_f32=0.01  [const]
    R3542    raw=  20968  int16=  20968           GE_f32=0.00  [const]
    R3543    raw=   4251  int16=   4251           GE_f32=0.00  [const]
    R3545    raw=  26624  int16=  26624           GE_f32=0.00  [const]
    R3547    raw=      3  int16=      3           GE_f32=0.00  [const]
    R3548    raw=   6144  int16=   6144           GE_f32=0.01  [const]
    R3549    raw=  15351  int16=  15351           GE_f32=0.00  [const]
    R3550    raw=  12700  int16=  12700           GE_f32=0.00  [const]
    R3554    raw=  21155  int16=  21155           GE_f32=0.00  [const]
    R3561    raw=  18115  int16=  18115           GE_f32=0.00  [const]
    R3586    raw=  64377  int16=  -1159           GE_f32=0.00  [const]
    R3587    raw=     11  int16=     11           GE_f32=0.00  [const]
    R5106    raw=   2413  int16=   2413           GE_f32=0.00  [const]
    R5108    raw=  64536  int16=  -1000           GE_f32=0.00  [const]
    R5230    raw=      2  int16=      2           GE_f32=0.00  [const]
    R5231    raw=     24  int16=     24           GE_f32=0.00  [const]
    R5232    raw=   2048  int16=   2048           GE_f32=0.00  [const]
    R5235    raw=  18000  int16=  18000          GE_f32=-0.00  [const]
    R5238    raw=  18000  int16=  18000           GE_f32=0.00  [const]
    R5240    raw=     24  int16=     24           GE_f32=0.00  [const]
    R5242    raw=  18432  int16=  18432           GE_f32=0.00  [const]
    R5244    raw=  18000  int16=  18000           GE_f32=0.00  [const]
    R5247    raw=     24  int16=     24           GE_f32=0.00  [const]
    R5248    raw=   2048  int16=   2048           GE_f32=0.00  [const]
    R5249    raw=      7  int16=      7           GE_f32=0.00  [const]
    R5251    raw=  18432  int16=  18432           GE_f32=0.00  [const]
    R5253    raw=  18000  int16=  18000           GE_f32=0.00  [const]
    R5256    raw=      2  int16=      2           GE_f32=0.00  [const]
    R5257    raw=   2048  int16=   2048           GE_f32=0.00  [const]
    R5259    raw=     24  int16=     24           GE_f32=0.00  [const]
    R5260    raw=   2048  int16=   2048           GE_f32=0.00  [const]
    R5261    raw=     30  int16=     30           GE_f32=0.00  [const]
    R5263    raw=  18432  int16=  18432           GE_f32=0.00  [const]
    R5265    raw=  18000  int16=  18000           GE_f32=0.00  [const]
    R5268    raw=      2  int16=      2           GE_f32=0.00  [const]
    R5269    raw=   2048  int16=   2048           GE_f32=0.00  [const]
    R5271    raw=     24  int16=     24           GE_f32=0.00  [const]
    R5272    raw=   2048  int16=   2048           GE_f32=0.00  [const]
    R5273    raw=    120  int16=    120           GE_f32=0.00  [const]
    R5275    raw=  18432  int16=  18432           GE_f32=0.00  [const]
    R5277    raw=  18000  int16=  18000          GE_f32=-0.00  [const]
    R5279    raw=      1  int16=      1           GE_f32=0.00  [const]
    R5280    raw=      2  int16=      2           GE_f32=0.00  [const]
    R5281    raw=   2048  int16=   2048           GE_f32=0.00  [const]
    R5282    raw=      2  int16=      2           GE_f32=0.00  [const]
    R5283    raw=     24  int16=     24           GE_f32=0.00  [const]
    R5284    raw=   2048  int16=   2048           GE_f32=0.00  [const]
    R5285    raw=    302  int16=    302           GE_f32=0.00  [const]
    R5286    raw=    500  int16=    500           GE_f32=0.00  [const]
    R5287    raw=   2048  int16=   2048           GE_f32=0.00  [const]
    R5499    raw=   3411  int16=   3411           GE_f32=0.00  [const]
    R5501    raw=    302  int16=    302           GE_f32=0.00  [const]
    R5505    raw=    140  int16=    140           GE_f32=0.00  [const]
    R5506    raw=    124  int16=    124           GE_f32=0.00  [const]
    R5522    raw=  65496  int16=    -40           GE_f32=0.00  [const]
    R5545    raw=      1  int16=      1           GE_f32=0.00  [const]
    R5549    raw=      3  int16=      3           GE_f32=0.00  [const]
    R5550    raw=      2  int16=      2           GE_f32=0.00  [const]
    R5551    raw=   2026  int16=   2026           GE_f32=0.00  [const]

  RPM_INT:
    R0       raw=      4  int16=      4           GE_f32=0.00  [const]
    R2       raw=      8  int16=      8           GE_f32=0.00  [const]
    R3       raw=     13  int16=     13           GE_f32=0.00  [const]
    R7       raw=      6  int16=      6           GE_f32=0.00  [const]
    R8       raw=      8  int16=      8           GE_f32=0.00  [const]
    R9       raw=     10  int16=     10           GE_f32=0.00  [const]
    R13      raw=      1  int16=      1           GE_f32=0.00  [const]
    R18      raw=      2  int16=      2           GE_f32=0.00  [const]
    R31      raw=     25  int16=     25           GE_f32=0.00  [const]
    R34      raw=     25  int16=     25           GE_f32=0.00  [const]
    R37      raw=     25  int16=     25           GE_f32=0.00  [const]
    R40      raw=     25  int16=     25           GE_f32=0.00  [const]
    R43      raw=     25  int16=     25           GE_f32=0.00  [const]
    R46      raw=     25  int16=     25           GE_f32=0.00  [const]
    R49      raw=     25  int16=     25           GE_f32=0.00  [const]
    R52      raw=     25  int16=     25           GE_f32=0.00  [const]
    R55      raw=     25  int16=     25           GE_f32=0.00  [const]
    R61      raw=     25  int16=     25           GE_f32=0.00  [const]
    R64      raw=     25  int16=     25           GE_f32=0.00  [const]
    R67      raw=     25  int16=     25           GE_f32=0.00  [const]
    R70      raw=     25  int16=     25           GE_f32=0.00  [const]
    R73      raw=     25  int16=     25           GE_f32=0.00  [const]
    R76      raw=     25  int16=     25           GE_f32=0.00  [const]
    R79      raw=     25  int16=     25           GE_f32=0.00  [const]
    R82      raw=     25  int16=     25           GE_f32=0.00  [const]
    R85      raw=     25  int16=     25           GE_f32=0.00  [const]
    R88      raw=     25  int16=     25           GE_f32=0.00  [const]
    R91      raw=     25  int16=     25           GE_f32=0.00  [const]
    R94      raw=     25  int16=     25           GE_f32=0.00  [const]
    R97      raw=     25  int16=     25           GE_f32=0.00  [const]
    R100     raw=     25  int16=     25           GE_f32=0.00  [const]
    R103     raw=     50  int16=     50           GE_f32=0.00  [const]
    R107     raw=      5  int16=      5           GE_f32=0.00  [const]
    R108     raw=     50  int16=     50           GE_f32=0.00  [const]
    R110     raw=     25  int16=     25           GE_f32=0.00  [const]
    R118     raw=     10  int16=     10           GE_f32=0.00  [const]
    R124     raw=    100  int16=    100           GE_f32=0.00  [const]
    R151     raw=      5  int16=      5           GE_f32=0.00  [const]
    R152     raw=      5  int16=      5           GE_f32=0.00  [const]
    R153     raw=     76  int16=     76                        [const]
    R157     raw=     46  int16=     46           GE_f32=0.00  [VARIES]
    R175     raw=      5  int16=      5           GE_f32=0.00  [const]
    R181     raw=     46  int16=     46           GE_f32=0.00  [VARIES]
    R185     raw=     20  int16=     20           GE_f32=0.00  [const]
    R188     raw=      5  int16=      5                        [const]
    R206     raw=      5  int16=      5           GE_f32=0.00  [const]
    R207     raw=      5  int16=      5           GE_f32=0.00  [const]
    R212     raw=     30  int16=     30           GE_f32=0.00  [const]
    R219     raw=      5  int16=      5           GE_f32=0.00  [const]
    R222     raw=      5  int16=      5           GE_f32=0.00  [const]
    R246     raw=     30  int16=     30           GE_f32=0.00  [const]
    R252     raw=     50  int16=     50           GE_f32=0.00  [const]
    R297     raw=     80  int16=     80           GE_f32=0.00  [const]
    R298     raw=     80  int16=     80           GE_f32=0.00  [const]
    R299     raw=     95  int16=     95           GE_f32=0.00  [const]
    R301     raw=     80  int16=     80           GE_f32=0.00  [const]
    R304     raw=     10  int16=     10                        [const]
    R313     raw=      5  int16=      5           GE_f32=0.00  [const]
    R316     raw=      5  int16=      5           GE_f32=0.00  [const]
    R382     raw=     20  int16=     20           GE_f32=0.00  [const]
    R385     raw=     20  int16=     20           GE_f32=0.00  [const]
    R394     raw=     20  int16=     20           GE_f32=0.00  [const]
    R400     raw=      3  int16=      3           GE_f32=0.00  [const]
    R442     raw=      5  int16=      5           GE_f32=0.00  [const]
    R534     raw=     50  int16=     50           GE_f32=0.00  [const]
    R540     raw=     10  int16=     10           GE_f32=0.00  [const]
    R543     raw=     10  int16=     10           GE_f32=0.00  [const]
    R546     raw=     80  int16=     80           GE_f32=0.00  [const]
    R549     raw=     80  int16=     80           GE_f32=0.00  [const]
    R552     raw=     80  int16=     80           GE_f32=0.00  [const]
    R555     raw=     80  int16=     80           GE_f32=0.00  [const]
    R558     raw=     80  int16=     80           GE_f32=0.00  [const]
    R561     raw=     80  int16=     80           GE_f32=0.00  [const]
    R564     raw=     80  int16=     80           GE_f32=0.00  [const]
    R567     raw=     80  int16=     80           GE_f32=0.00  [const]
    R570     raw=     80  int16=     80           GE_f32=0.00  [const]
    R573     raw=     80  int16=     80           GE_f32=0.00  [const]
    R576     raw=     80  int16=     80           GE_f32=0.00  [const]
    R579     raw=     80  int16=     80           GE_f32=0.00  [const]
    R582     raw=     80  int16=     80           GE_f32=0.00  [const]
    R585     raw=     80  int16=     80           GE_f32=0.00  [const]
    R588     raw=     80  int16=     80           GE_f32=0.00  [const]
    R591     raw=     80  int16=     80           GE_f32=0.00  [const]
    R594     raw=     80  int16=     80           GE_f32=0.00  [const]
    R597     raw=     80  int16=     80           GE_f32=0.00  [const]
    R599     raw=     15  int16=     15           GE_f32=0.00  [const]
    R610     raw=     80  int16=     80           GE_f32=0.00  [const]
    R613     raw=     80  int16=     80           GE_f32=0.00  [const]
    R616     raw=     80  int16=     80           GE_f32=0.00  [const]
    R619     raw=     80  int16=     80           GE_f32=0.00  [const]
    R622     raw=     80  int16=     80           GE_f32=0.00  [const]
    R625     raw=     80  int16=     80           GE_f32=0.00  [const]
    R628     raw=     80  int16=     80           GE_f32=0.00  [const]
    R646     raw=     80  int16=     80           GE_f32=0.00  [const]
    R649     raw=     80  int16=     80           GE_f32=0.00  [const]
    R658     raw=     50  int16=     50                        [const]
    R677     raw=    100  int16=    100           GE_f32=0.00  [const]
    R679     raw=     50  int16=     50           GE_f32=0.00  [const]
    R682     raw=     50  int16=     50           GE_f32=0.00  [const]
    R685     raw=     30  int16=     30           GE_f32=0.00  [const]
    R688     raw=     30  int16=     30           GE_f32=0.00  [const]
    R691     raw=     10  int16=     10           GE_f32=0.00  [const]
    R693     raw=      3  int16=      3           GE_f32=0.00  [const]
    R694     raw=      3  int16=      3           GE_f32=0.00  [const]
    R695     raw=     24  int16=     24           GE_f32=0.00  [const]
    R697     raw=     30  int16=     30           GE_f32=0.00  [const]
    R700     raw=     30  int16=     30           GE_f32=0.00  [const]
    R703     raw=     30  int16=     30           GE_f32=0.00  [const]
    R706     raw=     30  int16=     30           GE_f32=0.00  [const]
    R709     raw=     30  int16=     30           GE_f32=0.00  [const]
    R712     raw=     30  int16=     30           GE_f32=0.00  [const]
    R715     raw=     30  int16=     30           GE_f32=0.00  [const]
    R718     raw=     30  int16=     30           GE_f32=0.00  [const]
    R721     raw=     30  int16=     30           GE_f32=0.00  [const]
    R724     raw=     30  int16=     30           GE_f32=0.00  [const]
    R727     raw=     30  int16=     30           GE_f32=0.00  [const]
    R730     raw=     30  int16=     30           GE_f32=0.00  [const]
    R864     raw=     50  int16=     50           GE_f32=0.00  [const]
    R865     raw=     50  int16=     50           GE_f32=0.00  [const]
    R883     raw=      2  int16=      2           GE_f32=0.00  [const]
    R884     raw=      2  int16=      2           GE_f32=0.00  [const]
    R928     raw=      4  int16=      4           GE_f32=0.00  [const]
    R932     raw=      5  int16=      5                        [const]
    R934     raw=      8  int16=      8           GE_f32=0.00  [const]
    R935     raw=      8  int16=      8           GE_f32=0.00  [const]
    R936     raw=    151  int16=    151           GE_f32=0.00  [const]
    R1151    raw=      4  int16=      4           GE_f32=0.00  [const]
    R1153    raw=     30  int16=     30           GE_f32=0.00  [const]
    R1154    raw=     30  int16=     30                        [const]
    R1157    raw=      3  int16=      3                        [const]
    R1159    raw=     31  int16=     31           GE_f32=0.00  [const]
    R1160    raw=      1  int16=      1           GE_f32=0.00  [const]
    R1162    raw=    100  int16=    100           GE_f32=0.00  [const]
    R1236    raw=      8  int16=      8           GE_f32=0.00  [const]
    R1238    raw=      5  int16=      5           GE_f32=0.00  [const]
    R1241    raw=      2  int16=      2           GE_f32=0.00  [const]
    R1449    raw=     80  int16=     80           GE_f32=0.00  [const]
    R1452    raw=     80  int16=     80           GE_f32=0.00  [const]
    R2008    raw=     78  int16=     78                        [const]
    R2011    raw=     15  int16=     15           GE_f32=0.00  [const]
    R2027    raw=      1  int16=      1           GE_f32=0.00  [const]
    R2028    raw=      1  int16=      1           GE_f32=0.00  [const]
    R2029    raw=     65  int16=     65           GE_f32=0.00  [const]
    R2032    raw=     10  int16=     10           GE_f32=0.00  [const]
    R2046    raw=    108  int16=    108           GE_f32=0.01  [const]
    R2054    raw=      2  int16=      2           GE_f32=0.00  [const]
    R2056    raw=     12  int16=     12                        [const]
    R2067    raw=      4  int16=      4           GE_f32=0.00  [const]
    R2077    raw=     16  int16=     16           GE_f32=0.00  [const]
    R2096    raw=      2  int16=      2           GE_f32=0.00  [const]
    R2107    raw=      1  int16=      1           GE_f32=0.00  [const]
    R2116    raw=    187  int16=    187           GE_f32=0.00  [const]
    R2119    raw=     15  int16=     15           GE_f32=0.00  [const]
    R2136    raw=      5  int16=      5          GE_f32=-2.00  [const]
    R2139    raw=     55  int16=     55           GE_f32=0.00  [const]
    R2140    raw=     55  int16=     55           GE_f32=0.00  [const]
    R2143    raw=      5  int16=      5          GE_f32=-2.00  [const]
    R2147    raw=     50  int16=     50           GE_f32=0.00  [const]
    R2152    raw=     25  int16=     25           GE_f32=0.00  [const]
    R2153    raw=     25  int16=     25           GE_f32=0.00  [const]
    R2197    raw=      7  int16=      7           GE_f32=0.00  [const]
    R2198    raw=      7  int16=      7           GE_f32=0.00  [const]
    R2199    raw=    102  int16=    102           GE_f32=0.00  [const]
    R3425    raw=      5  int16=      5           GE_f32=0.00  [const]
    R3430    raw=     47  int16=     47           GE_f32=0.00  [VARIES]
    R3445    raw=      3  int16=      3           GE_f32=0.00  [const]
    R3448    raw=      2  int16=      2           GE_f32=0.00  [const]
    R3449    raw=      2  int16=      2           GE_f32=0.00  [const]
    R3450    raw=     17  int16=     17           GE_f32=0.00  [const]
    R3451    raw=     44  int16=     44           GE_f32=0.00  [const]
    R3452    raw=      5  int16=      5           GE_f32=0.00  [const]
    R3453    raw=      5  int16=      5           GE_f32=0.00  [const]
    R3457    raw=     10  int16=     10           GE_f32=0.00  [const]
    R3460    raw=     10  int16=     10           GE_f32=0.00  [const]
    R3470    raw=      5  int16=      5           GE_f32=0.00  [const]
    R3471    raw=      5  int16=      5           GE_f32=0.00  [const]
    R3474    raw=     15  int16=     15                        [const]
    R3477    raw=     15  int16=     15           GE_f32=0.00  [const]
    R3494    raw=      2  int16=      2                        [const]
    R3514    raw=     21  int16=     21           GE_f32=0.00  [const]
    R3541    raw=     45  int16=     45                        [const]
    R3544    raw=      5  int16=      5                        [const]
    R3547    raw=      3  int16=      3           GE_f32=0.00  [const]
    R3587    raw=     11  int16=     11           GE_f32=0.00  [const]
    R3588    raw=     15  int16=     15                        [const]
    R5230    raw=      2  int16=      2           GE_f32=0.00  [const]
    R5231    raw=     24  int16=     24           GE_f32=0.00  [const]
    R5240    raw=     24  int16=     24           GE_f32=0.00  [const]
    R5241    raw=     24  int16=     24      GE_f32=131072.38  [const]
    R5247    raw=     24  int16=     24           GE_f32=0.00  [const]
    R5249    raw=      7  int16=      7           GE_f32=0.00  [const]
    R5250    raw=      7  int16=      7      GE_f32=131072.11  [const]
    R5256    raw=      2  int16=      2           GE_f32=0.00  [const]
    R5259    raw=     24  int16=     24           GE_f32=0.00  [const]
    R5261    raw=     30  int16=     30           GE_f32=0.00  [const]
    R5262    raw=     30  int16=     30      GE_f32=131072.47  [const]
    R5268    raw=      2  int16=      2           GE_f32=0.00  [const]
    R5271    raw=     24  int16=     24           GE_f32=0.00  [const]
    R5273    raw=    120  int16=    120           GE_f32=0.00  [const]
    R5274    raw=    120  int16=    120      GE_f32=131073.88  [const]
    R5279    raw=      1  int16=      1           GE_f32=0.00  [const]
    R5280    raw=      2  int16=      2           GE_f32=0.00  [const]
    R5282    raw=      2  int16=      2           GE_f32=0.00  [const]
    R5283    raw=     24  int16=     24           GE_f32=0.00  [const]
    R5505    raw=    140  int16=    140           GE_f32=0.00  [const]
    R5506    raw=    124  int16=    124           GE_f32=0.00  [const]
    R5545    raw=      1  int16=      1           GE_f32=0.00  [const]
    R5549    raw=      3  int16=      3           GE_f32=0.00  [const]
    R5550    raw=      2  int16=      2           GE_f32=0.00  [const]

  TEMPERATURE:
    R417     raw=  32768  int16= -32768         GE_f32=321.00  [const]

  STATE/MODE:
    R0       raw=      4  int16=      4           GE_f32=0.00  [const]
    R2       raw=      8  int16=      8           GE_f32=0.00  [const]
    R3       raw=     13  int16=     13           GE_f32=0.00  [const]
    R7       raw=      6  int16=      6           GE_f32=0.00  [const]
    R8       raw=      8  int16=      8           GE_f32=0.00  [const]
    R9       raw=     10  int16=     10           GE_f32=0.00  [const]
    R13      raw=      1  int16=      1           GE_f32=0.00  [const]
    R18      raw=      2  int16=      2           GE_f32=0.00  [const]
    R107     raw=      5  int16=      5           GE_f32=0.00  [const]
    R118     raw=     10  int16=     10           GE_f32=0.00  [const]
    R151     raw=      5  int16=      5           GE_f32=0.00  [const]
    R152     raw=      5  int16=      5           GE_f32=0.00  [const]
    R175     raw=      5  int16=      5           GE_f32=0.00  [const]
    R188     raw=      5  int16=      5                        [const]
    R206     raw=      5  int16=      5           GE_f32=0.00  [const]
    R207     raw=      5  int16=      5           GE_f32=0.00  [const]
    R219     raw=      5  int16=      5           GE_f32=0.00  [const]
    R222     raw=      5  int16=      5           GE_f32=0.00  [const]
    R304     raw=     10  int16=     10                        [const]
    R313     raw=      5  int16=      5           GE_f32=0.00  [const]
    R316     raw=      5  int16=      5           GE_f32=0.00  [const]
    R400     raw=      3  int16=      3           GE_f32=0.00  [const]
    R442     raw=      5  int16=      5           GE_f32=0.00  [const]
    R540     raw=     10  int16=     10           GE_f32=0.00  [const]
    R543     raw=     10  int16=     10           GE_f32=0.00  [const]
    R691     raw=     10  int16=     10           GE_f32=0.00  [const]
    R693     raw=      3  int16=      3           GE_f32=0.00  [const]
    R694     raw=      3  int16=      3           GE_f32=0.00  [const]
    R883     raw=      2  int16=      2           GE_f32=0.00  [const]
    R884     raw=      2  int16=      2           GE_f32=0.00  [const]
    R928     raw=      4  int16=      4           GE_f32=0.00  [const]
    R932     raw=      5  int16=      5                        [const]
    R934     raw=      8  int16=      8           GE_f32=0.00  [const]
    R935     raw=      8  int16=      8           GE_f32=0.00  [const]
    R1151    raw=      4  int16=      4           GE_f32=0.00  [const]
    R1157    raw=      3  int16=      3                        [const]
    R1160    raw=      1  int16=      1           GE_f32=0.00  [const]
    R1236    raw=      8  int16=      8           GE_f32=0.00  [const]
    R1238    raw=      5  int16=      5           GE_f32=0.00  [const]
    R1241    raw=      2  int16=      2           GE_f32=0.00  [const]
    R2027    raw=      1  int16=      1           GE_f32=0.00  [const]
    R2028    raw=      1  int16=      1           GE_f32=0.00  [const]
    R2032    raw=     10  int16=     10           GE_f32=0.00  [const]
    R2054    raw=      2  int16=      2           GE_f32=0.00  [const]
    R2056    raw=     12  int16=     12                        [const]
    R2067    raw=      4  int16=      4           GE_f32=0.00  [const]
    R2096    raw=      2  int16=      2           GE_f32=0.00  [const]
    R2107    raw=      1  int16=      1           GE_f32=0.00  [const]
    R2136    raw=      5  int16=      5          GE_f32=-2.00  [const]
    R2143    raw=      5  int16=      5          GE_f32=-2.00  [const]
    R2197    raw=      7  int16=      7           GE_f32=0.00  [const]
    R2198    raw=      7  int16=      7           GE_f32=0.00  [const]
    R3425    raw=      5  int16=      5           GE_f32=0.00  [const]
    R3445    raw=      3  int16=      3           GE_f32=0.00  [const]
    R3448    raw=      2  int16=      2           GE_f32=0.00  [const]
    R3449    raw=      2  int16=      2           GE_f32=0.00  [const]
    R3452    raw=      5  int16=      5           GE_f32=0.00  [const]
    R3453    raw=      5  int16=      5           GE_f32=0.00  [const]
    R3457    raw=     10  int16=     10           GE_f32=0.00  [const]
    R3460    raw=     10  int16=     10           GE_f32=0.00  [const]
    R3470    raw=      5  int16=      5           GE_f32=0.00  [const]
    R3471    raw=      5  int16=      5           GE_f32=0.00  [const]
    R3494    raw=      2  int16=      2                        [const]
    R3544    raw=      5  int16=      5                        [const]
    R3547    raw=      3  int16=      3           GE_f32=0.00  [const]
    R3587    raw=     11  int16=     11           GE_f32=0.00  [const]
    R5230    raw=      2  int16=      2           GE_f32=0.00  [const]
    R5249    raw=      7  int16=      7           GE_f32=0.00  [const]
    R5250    raw=      7  int16=      7      GE_f32=131072.11  [const]
    R5256    raw=      2  int16=      2           GE_f32=0.00  [const]
    R5268    raw=      2  int16=      2           GE_f32=0.00  [const]
    R5279    raw=      1  int16=      1           GE_f32=0.00  [const]
    R5280    raw=      2  int16=      2           GE_f32=0.00  [const]
    R5282    raw=      2  int16=      2           GE_f32=0.00  [const]
    R5545    raw=      1  int16=      1           GE_f32=0.00  [const]
    R5549    raw=      3  int16=      3           GE_f32=0.00  [const]
    R5550    raw=      2  int16=      2           GE_f32=0.00  [const]

  ENCODER/COUNTER:
    R140     raw=  57344  int16=  -8192        GE_f32=1055.00  [VARIES]
    R158     raw=   7360  int16=   7360           GE_f32=0.00  [VARIES]
    R159     raw=  14720  int16=  14720           GE_f32=0.00  [VARIES]
    R163     raw=  49152  int16= -16384       GE_f32=18144.00  [VARIES]
    R165     raw=   9961  int16=   9961           GE_f32=0.57  [VARIES]
    R167     raw=  28180  int16=  28180         GE_f32=164.43  [VARIES]
    R169     raw=  56360  int16=  -9176         GE_f32=124.43  [VARIES]
    R187     raw=   6621  int16=   6621           GE_f32=0.00  [VARIES]
    R189     raw=  24576  int16=  24576       GE_f32=19376.00  [VARIES]
    R195     raw=   4194  int16=   4194           GE_f32=0.11  [VARIES]
    R256     raw=  51872  int16= -13664      GE_f32=335445.00  [VARIES]
    R258     raw=  19098  int16=  19098      GE_f32=709801.62  [VARIES]
    R260     raw=  23608  int16=  23608      GE_f32=771523.50  [VARIES]
    R262     raw=   3619  int16=   3619       GE_f32=29575.07  [VARIES]
    R266     raw=   6189  int16=   6189        GE_f32=9414.04  [VARIES]
    R270     raw=  23010  int16=  23010        GE_f32=7531.24  [VARIES]
    R433     raw=  61440  int16=  -4096       GE_f32=19832.00  [VARIES]
    R435     raw=  42992  int16= -22544           GE_f32=0.62  [VARIES]
    R437     raw=  47678  int16= -17858         GE_f32=179.73  [VARIES]
    R439     raw=  47678  int16= -17858         GE_f32=139.73  [VARIES]
    R502     raw=  52782  int16= -12754        GE_f32=8089.77  [VARIES]
    R506     raw=   7184  int16=   7184                        [VARIES]
    R507     raw=  28597  int16=  28597           GE_f32=0.25  [VARIES]
    R511     raw=  58196  int16=  -7340           GE_f32=0.22  [VARIES]
    R607     raw=  42916  int16= -22620        GE_f32=8233.91  [VARIES]
    R2070    raw=  29300  int16=  29300           GE_f32=0.00  [VARIES]
    R2073    raw=  29792  int16=  29792           GE_f32=0.00  [VARIES]
    R2074    raw=   4478  int16=   4478           GE_f32=0.00  [VARIES]
    R2078    raw=   4434  int16=   4434           GE_f32=0.00  [VARIES]
    R3539    raw=  15443  int16=  15443          GE_f32=24.15  [VARIES]
    R3558    raw=  28804  int16=  28804          GE_f32=46.61  [VARIES]
    R3584    raw=  30481  int16=  30481                        [VARIES]
    R3589    raw=  30481  int16=  30481           GE_f32=0.00  [VARIES]
    R3590    raw=   8711  int16=   8711                        [VARIES]
    R3591    raw=  30481  int16=  30481           GE_f32=0.00  [VARIES]
    R5234    raw=  14710  int16=  14710       GE_f32=13326.37  [VARIES]
    R5236    raw=  35248  int16= -30288           GE_f32=0.00  [VARIES]
    R5276    raw=  14727  int16=  14727       GE_f32=13326.38  [VARIES]
    R5278    raw=  33553  int16= -31983           GE_f32=0.00  [VARIES]
    R5520    raw=  40203  int16= -25333        GE_f32=9639.26  [VARIES]
    R5529    raw=  28804  int16=  28804          GE_f32=46.61  [VARIES]

  CONFIG/FW:
    R6       raw=  57857  int16=  -7679           GE_f32=0.00  [const]
    R130     raw=  20682  int16=  20682          GE_f32=22.79  [const]
    R131     raw=  16822  int16=  16822           GE_f32=0.00  [const]
    R145     raw=  12700  int16=  12700                        [const]
    R146     raw=  21460  int16=  21460           GE_f32=0.00  [const]
    R154     raw=  32767  int16=  32767           GE_f32=0.00  [const]
    R156     raw=  52660  int16= -12876           GE_f32=0.00  [const]
    R161     raw=  21460  int16=  21460       GE_f32=28713.91  [const]
    R164     raw=  18061  int16=  18061           GE_f32=0.00  [const]
    R168     raw=  17188  int16=  17188                        [const]
    R170     raw=  17144  int16=  17144           GE_f32=0.00  [const]
    R173     raw=  65531  int16=     -5           GE_f32=0.00  [const]
    R178     raw=  17297  int16=  17297           GE_f32=0.00  [const]
    R180     raw=  16928  int16=  16928           GE_f32=0.00  [const]
    R197     raw=  65531  int16=     -5           GE_f32=0.00  [const]
    R226     raw=  21460  int16=  21460           GE_f32=0.00  [const]
    R228     raw=  12024  int16=  12024           GE_f32=0.00  [const]
    R264     raw=  20888  int16=  20888           GE_f32=0.00  [const]
    R293     raw=  31730  int16=  31730           GE_f32=0.00  [const]
    R303     raw=  32767  int16=  32767           GE_f32=0.00  [const]
    R305     raw=  64776  int16=   -760           GE_f32=0.00  [const]
    R307     raw=  16331  int16=  16331                        [const]
    R308     raw=  58347  int16=  -7189       GE_f32=31729.96  [const]
    R309     raw=  18167  int16=  18167           GE_f32=0.00  [const]
    R380     raw=  49696  int16= -15840           GE_f32=0.00  [const]
    R387     raw=  32767  int16=  32767           GE_f32=0.00  [const]
    R389     raw=  63921  int16=  -1615           GE_f32=0.00  [const]
    R392     raw=  17633  int16=  17633           GE_f32=0.00  [const]
    R396     raw=  32767  int16=  32767           GE_f32=0.00  [const]
    R398     raw=  63922  int16=  -1614           GE_f32=0.00  [const]
    R417     raw=  32768  int16= -32768         GE_f32=321.00  [const]
    R418     raw=  17312  int16=  17312           GE_f32=0.00  [const]
    R434     raw=  18074  int16=  18074          GE_f32=-0.00  [const]
    R436     raw=  16158  int16=  16158          GE_f32=-0.00  [const]
    R438     raw=  17203  int16=  17203          GE_f32=-0.00  [const]
    R440     raw=  17163  int16=  17163           GE_f32=0.00  [const]
    R483     raw=  32767  int16=  32767           GE_f32=0.00  [const]
    R485     raw=  63921  int16=  -1615           GE_f32=0.00  [const]
    R498     raw=  43008  int16= -22528       GE_f32=21460.00  [const]
    R499     raw=  18087  int16=  18087           GE_f32=0.00  [const]
    R520     raw=  17530  int16=  17530           GE_f32=0.00  [const]
    R522     raw=  17633  int16=  17633           GE_f32=2.00  [const]
    R523     raw=  16384  int16=  16384        GE_f32=1250.00  [const]
    R524     raw=  17564  int16=  17564           GE_f32=2.00  [const]
    R525     raw=  16384  int16=  16384        GE_f32=1250.00  [const]
    R526     raw=  17564  int16=  17564           GE_f32=0.00  [const]
    R528     raw=  17633  int16=  17633           GE_f32=0.00  [const]
    R530     raw=  17549  int16=  17549           GE_f32=0.00  [const]
    R532     raw=  17633  int16=  17633           GE_f32=0.00  [const]
    R632     raw=  49152  int16= -16384       GE_f32=28000.00  [const]
    R633     raw=  18138  int16=  18138           GE_f32=0.00  [const]
    R642     raw=  40960  int16= -24576       GE_f32=18000.00  [const]
    R643     raw=  18060  int16=  18060           GE_f32=0.00  [const]
    R659     raw=  65486  int16=    -50           GE_f32=0.00  [const]
    R863     raw=  16822  int16=  16822           GE_f32=0.00  [const]
    R867     raw=  16202  int16=  16202           GE_f32=0.00  [const]
    R897     raw=  65411  int16=   -125         GE_f32=619.99  [const]
    R898     raw=  17434  int16=  17434           GE_f32=0.79  [const]
    R899     raw=  16202  int16=  16202           GE_f32=0.00  [const]
    R931     raw=  32767  int16=  32767           GE_f32=0.00  [const]
    R933     raw=  64500  int16=  -1036           GE_f32=0.00  [const]
    R1155    raw=  65506  int16=    -30                        [const]
    R1156    raw=  65507  int16=    -29           GE_f32=0.00  [const]
    R1158    raw=  65535  int16=     -1           GE_f32=0.00  [const]
    R1271    raw=  40960  int16= -24576       GE_f32=18000.00  [const]
    R1272    raw=  18060  int16=  18060           GE_f32=0.00  [const]
    R1371    raw=  20682  int16=  20682          GE_f32=22.79  [const]
    R1372    raw=  16822  int16=  16822           GE_f32=0.00  [const]
    R2000    raw=  20968  int16=  20968           GE_f32=0.00  [const]
    R2003    raw=  20968  int16=  20968           GE_f32=0.00  [const]
    R2005    raw=  20968  int16=  20968           GE_f32=0.00  [const]
    R2007    raw=  21192  int16=  21192           GE_f32=0.00  [const]
    R2009    raw=  20968  int16=  20968                        [const]
    R2010    raw=  62526  int16=  -3010           GE_f32=0.00  [const]
    R2033    raw=  13700  int16=  13700           GE_f32=0.00  [const]
    R2034    raw=  15151  int16=  15151                        [const]
    R2035    raw=  32000  int16=  32000           GE_f32=0.00  [const]
    R2036    raw=  12700  int16=  12700           GE_f32=0.00  [const]
    R2038    raw=  15451  int16=  15451           GE_f32=0.00  [const]
    R2039    raw=  13420  int16=  13420           GE_f32=0.01  [const]
    R2040    raw=  15351  int16=  15351           GE_f32=0.00  [const]
    R2042    raw=  20682  int16=  20682           GE_f32=0.00  [const]
    R2047    raw=  15451  int16=  15451           GE_f32=0.00  [const]
    R2051    raw=  32000  int16=  32000           GE_f32=0.00  [const]
    R2057    raw=  65524  int16=    -12           GE_f32=0.00  [const]
    R2104    raw=  16822  int16=  16822           GE_f32=0.00  [const]
    R2105    raw=  13000  int16=  13000           GE_f32=0.00  [const]
    R2108    raw=  13000  int16=  13000           GE_f32=0.00  [const]
    R2110    raw=  60842  int16=  -4694           GE_f32=0.00  [const]
    R2111    raw=  13000  int16=  13000                        [const]
    R2112    raw=  60934  int16=  -4602           GE_f32=0.00  [const]
    R2113    raw=  13000  int16=  13000           GE_f32=0.00  [const]
    R2117    raw=  13000  int16=  13000                        [const]
    R2118    raw=  62510  int16=  -3026           GE_f32=0.00  [const]
    R2137    raw=  49152  int16= -16384           GE_f32=0.00  [const]
    R2144    raw=  49152  int16= -16384           GE_f32=0.00  [const]
    R2145    raw=  13400  int16=  13400           GE_f32=0.00  [const]
    R2149    raw=  12600  int16=  12600                        [const]
    R2150    raw=  32000  int16=  32000           GE_f32=0.00  [const]
    R2194    raw=  17588  int16=  17588           GE_f32=0.00  [const]
    R2196    raw=  17588  int16=  17588           GE_f32=0.00  [const]
    R3400    raw=  28160  int16=  28160       GE_f32=21175.00  [const]
    R3401    raw=  18085  int16=  18085                        [const]
    R3402    raw=  22528  int16=  22528       GE_f32=16940.00  [const]
    R3403    raw=  18052  int16=  18052         GE_f32=617.10  [const]
    R3404    raw=  17434  int16=  17434           GE_f32=0.00  [const]
    R3405    raw=  13816  int16=  13816           GE_f32=0.00  [const]
    R3407    raw=  13000  int16=  13000           GE_f32=0.00  [const]
    R3408    raw=  13312  int16=  13312       GE_f32=17434.00  [const]
    R3409    raw=  18056  int16=  18056           GE_f32=0.00  [const]
    R3410    raw=  13312  int16=  13312       GE_f32=17434.00  [const]
    R3411    raw=  18056  int16=  18056                        [const]
    R3412    raw=  56320  int16=  -9216       GE_f32=15351.00  [const]
    R3413    raw=  18031  int16=  18031                        [const]
    R3414    raw=  57241  int16=  -8295       GE_f32=13815.90  [const]
    R3415    raw=  18007  int16=  18007           GE_f32=0.00  [const]
    R3421    raw=  17995  int16=  17995           GE_f32=0.00  [const]
    R3423    raw=  17995  int16=  17995           GE_f32=0.00  [const]
    R3427    raw=  28672  int16=  28672       GE_f32=12700.00  [const]
    R3428    raw=  17990  int16=  17990           GE_f32=0.00  [const]
    R3437    raw=  49152  int16= -16384       GE_f32=10160.00  [const]
    R3438    raw=  17950  int16=  17950           GE_f32=0.00  [const]
    R3439    raw=  10160  int16=  10160           GE_f32=0.00  [const]
    R3441    raw=  28672  int16=  28672       GE_f32=19128.00  [const]
    R3442    raw=  18069  int16=  18069           GE_f32=0.00  [const]
    R3468    raw=  18434  int16=  18434           GE_f32=0.00  [const]
    R3469    raw=  13608  int16=  13608           GE_f32=0.00  [const]
    R3473    raw=  32767  int16=  32767           GE_f32=0.00  [const]
    R3475    raw=  64023  int16=  -1513           GE_f32=0.00  [const]
    R3479    raw=  20480  int16=  20480        GE_f32=3013.00  [const]
    R3480    raw=  17724  int16=  17724           GE_f32=0.00  [const]
    R3495    raw=  65534  int16=     -2           GE_f32=0.00  [const]
    R3501    raw=  16822  int16=  16822           GE_f32=0.00  [const]
    R3531    raw=  16822  int16=  16822          GE_f32=17.91  [const]
    R3532    raw=  16783  int16=  16783          GE_f32=17.91  [const]
    R3533    raw=  16783  int16=  16783          GE_f32=22.78  [const]
    R3534    raw=  16822  int16=  16822           GE_f32=0.00  [const]
    R3535    raw=  13000  int16=  13000           GE_f32=0.34  [const]
    R3536    raw=  16046  int16=  16046      GE_f32=483829.44  [const]
    R3537    raw=  18668  int16=  18668                        [const]
    R3538    raw=  19062  int16=  19062           GE_f32=0.01  [const]
    R3542    raw=  20968  int16=  20968           GE_f32=0.00  [const]
    R3545    raw=  26624  int16=  26624           GE_f32=0.00  [const]
    R3549    raw=  15351  int16=  15351           GE_f32=0.00  [const]
    R3550    raw=  12700  int16=  12700           GE_f32=0.00  [const]
    R3553    raw=  18093  int16=  18093                        [const]
    R3554    raw=  21155  int16=  21155           GE_f32=0.00  [const]
    R3560    raw=  20480  int16=  20480       GE_f32=25000.00  [const]
    R3561    raw=  18115  int16=  18115           GE_f32=0.00  [const]
    R3585    raw=  65520  int16=    -16                        [const]
    R3586    raw=  64377  int16=  -1159           GE_f32=0.00  [const]
    R5108    raw=  64536  int16=  -1000           GE_f32=0.00  [const]
    R5235    raw=  18000  int16=  18000          GE_f32=-0.00  [const]
    R5238    raw=  18000  int16=  18000           GE_f32=0.00  [const]
    R5242    raw=  18432  int16=  18432           GE_f32=0.00  [const]
    R5244    raw=  18000  int16=  18000           GE_f32=0.00  [const]
    R5251    raw=  18432  int16=  18432           GE_f32=0.00  [const]
    R5253    raw=  18000  int16=  18000           GE_f32=0.00  [const]
    R5263    raw=  18432  int16=  18432           GE_f32=0.00  [const]
    R5265    raw=  18000  int16=  18000           GE_f32=0.00  [const]
    R5275    raw=  18432  int16=  18432           GE_f32=0.00  [const]
    R5277    raw=  18000  int16=  18000          GE_f32=-0.00  [const]
    R5522    raw=  65496  int16=    -40           GE_f32=0.00  [const]
    R5528    raw=  32000  int16=  32000                        [const]

  STATUS_FLAG:
    R6       raw=  57857  int16=  -7679           GE_f32=0.00  [const]
    R156     raw=  52660  int16= -12876           GE_f32=0.00  [const]
    R173     raw=  65531  int16=     -5           GE_f32=0.00  [const]
    R197     raw=  65531  int16=     -5           GE_f32=0.00  [const]
    R305     raw=  64776  int16=   -760           GE_f32=0.00  [const]
    R308     raw=  58347  int16=  -7189       GE_f32=31729.96  [const]
    R380     raw=  49696  int16= -15840           GE_f32=0.00  [const]
    R389     raw=  63921  int16=  -1615           GE_f32=0.00  [const]
    R398     raw=  63922  int16=  -1614           GE_f32=0.00  [const]
    R417     raw=  32768  int16= -32768         GE_f32=321.00  [const]
    R485     raw=  63921  int16=  -1615           GE_f32=0.00  [const]
    R498     raw=  43008  int16= -22528       GE_f32=21460.00  [const]
    R632     raw=  49152  int16= -16384       GE_f32=28000.00  [const]
    R642     raw=  40960  int16= -24576       GE_f32=18000.00  [const]
    R659     raw=  65486  int16=    -50           GE_f32=0.00  [const]
    R897     raw=  65411  int16=   -125         GE_f32=619.99  [const]
    R933     raw=  64500  int16=  -1036           GE_f32=0.00  [const]
    R1155    raw=  65506  int16=    -30                        [const]
    R1156    raw=  65507  int16=    -29           GE_f32=0.00  [const]
    R1158    raw=  65535  int16=     -1           GE_f32=0.00  [const]
    R1271    raw=  40960  int16= -24576       GE_f32=18000.00  [const]
    R2010    raw=  62526  int16=  -3010           GE_f32=0.00  [const]
    R2057    raw=  65524  int16=    -12           GE_f32=0.00  [const]
    R2110    raw=  60842  int16=  -4694           GE_f32=0.00  [const]
    R2112    raw=  60934  int16=  -4602           GE_f32=0.00  [const]
    R2118    raw=  62510  int16=  -3026           GE_f32=0.00  [const]
    R2137    raw=  49152  int16= -16384           GE_f32=0.00  [const]
    R2144    raw=  49152  int16= -16384           GE_f32=0.00  [const]
    R3412    raw=  56320  int16=  -9216       GE_f32=15351.00  [const]
    R3414    raw=  57241  int16=  -8295       GE_f32=13815.90  [const]
    R3437    raw=  49152  int16= -16384       GE_f32=10160.00  [const]
    R3475    raw=  64023  int16=  -1513           GE_f32=0.00  [const]
    R3495    raw=  65534  int16=     -2           GE_f32=0.00  [const]
    R3585    raw=  65520  int16=    -16                        [const]
    R3586    raw=  64377  int16=  -1159           GE_f32=0.00  [const]
    R5108    raw=  64536  int16=  -1000           GE_f32=0.00  [const]
    R5522    raw=  65496  int16=    -40           GE_f32=0.00  [const]

  UNKNOWN:
    R2069    raw=    500  int16=    500                        [const]

================================================================================
  HXI TEMPLATE COMPARISON (confirmed on Rig 709)
================================================================================
  R163    torque                 FLOAT32  ft-lbs               PRESENT  [VARIES]  GE=18144.00, STD=-2.00
  R165    turns                  FLOAT32  turns                PRESENT  [VARIES]  GE=0.57, STD=0.00
  R167    temperature            FLOAT32  degF                 PRESENT  [VARIES]  GE=164.43, STD=---
  R169    rpm                    FLOAT32  RPM                  PRESENT  [VARIES]  GE=124.43, STD=---
  R175    connection_state       INT16    enum 0-13            PRESENT  [const]  int16=5, raw=5
  R178    target_torque          INT16    ft-lbs               PRESENT  [const]  int16=17297, raw=17297
  R180    shoulder_torque        INT16    ft-lbs               PRESENT  [const]  int16=16928, raw=16928
  R185    connection_count       INT16    count                PRESENT  [const]  int16=20, raw=20
  R189    hookload               FLOAT32  lbs (x0.001 = klbs)  PRESENT  [VARIES]  GE=19376.00, STD=---
  R2076   encoder_counts_LSW     INT32    counts               PRESENT  [VARIES]  int16=6223, raw=6223
  R2077   encoder_counts_MSW     INT16    MSW                  PRESENT  [const]  int16=16, raw=16

================================================================================
  RAW SNAPSHOTS (all 5 passes)
================================================================================
  Addr        Pass1    Pass2    Pass3    Pass4    Pass5    Delta
  ──────── ──────── ──────── ──────── ──────── ──────── ────────
  R0             4        4        4        4        4        0
  R2             8        8        8        8        8        0
  R3            13       13       13       13       13        0
  R6         57857    57857    57857    57857    57857        0
  R7             6        6        6        6        6        0
  R8             8        8        8        8        8        0
  R9            10       10       10       10       10        0
  R10          257      257      257      257      257        0
  R13            1        1        1        1        1        0
  R14         7500     7500     7500     7500     7500        0
  R18            2        2        2        2        2        0
  R31           25       25       25       25       25        0
  R34           25       25       25       25       25        0
  R37           25       25       25       25       25        0
  R40           25       25       25       25       25        0
  R43           25       25       25       25       25        0
  R46           25       25       25       25       25        0
  R49           25       25       25       25       25        0
  R52           25       25       25       25       25        0
  R55           25       25       25       25       25        0
  R61           25       25       25       25       25        0
  R64           25       25       25       25       25        0
  R67           25       25       25       25       25        0
  R70           25       25       25       25       25        0
  R73           25       25       25       25       25        0
  R76           25       25       25       25       25        0
  R79           25       25       25       25       25        0
  R82           25       25       25       25       25        0
  R85           25       25       25       25       25        0
  R88           25       25       25       25       25        0
  R91           25       25       25       25       25        0
  R94           25       25       25       25       25        0
  R97           25       25       25       25       25        0
  R100          25       25       25       25       25        0
  R103          50       50       50       50       50        0
  R107           5        5        5        5        5        0
  R108          50       50       50       50       50        0
  R110          25       25       25       25       25        0
  R118          10       10       10       10       10        0
  R124         100      100      100      100      100        0
  R130       20682    20682    20682    20682    20682        0
  R131       16822    16822    16822    16822    16822        0
  R140        8192    12288    45056    45056    57344    49152  <-- CHANGING
  R141       17546    17540    17547    17542    17539        8  <-- CHANGING
  R145       12700    12700    12700    12700    12700        0
  R146       21460    21460    21460    21460    21460        0
  R151           5        5        5        5        5        0
  R152           5        5        5        5        5        0
  R153          76       76       76       76       76        0
  R154       32767    32767    32767    32767    32767        0
  R155        1000     1000     1000     1000     1000        0
  R156       52660    52660    52660    52660    52660        0
  R157          46       47       47       47       46        1  <-- CHANGING
  R158        7360     7520     7520     7520     7360      160  <-- CHANGING
  R159       14720    15040    15040    15040    14720      320  <-- CHANGING
  R161       21460    21460    21460    21460    21460        0
  R162       18128    18128    18120    18144    18144       24  <-- CHANGING
  R163       40960    40960    36864    49152    49152    12288  <-- CHANGING
  R164       18061    18061    18061    18061    18061        0
  R165        1573     1573    62915     9961     9961    61342  <-- CHANGING
  R166       16145    16145    16144    16145    16145        1  <-- CHANGING
  R167       18678    18678    13927    28180    28180    14253  <-- CHANGING
  R168       17188    17188    17188    17188    17188        0
  R169       37356    37356    27854    56360    56360    28506  <-- CHANGING
  R170       17144    17144    17144    17144    17144        0
  R173       65531    65531    65531    65531    65531        0
  R175           5        5        5        5        5        0
  R178       17297    17297    17297    17297    17297        0
  R180       16928    16928    16928    16928    16928        0
  R181          46       47       47       47       46        1  <-- CHANGING
  R185          20       20       20       20       20        0
  R187        6910     6765     7216     6856     6621      595  <-- CHANGING
  R188           5        5        5        5        5        0
  R189       40960    28672    61440    61440    24576    36864  <-- CHANGING
  R190       18072    18071    18072    18071    18071        1  <-- CHANGING
  R192       17757    17747    17759    17751    17747       12  <-- CHANGING
  R195       19923    37749    56623    44040     4194    52429  <-- CHANGING
  R196       15842    15832    15844    15836    15832       12  <-- CHANGING
  R197       65531    65531    65531    65531    65531        0
  R206           5        5        5        5        5        0
  R207           5        5        5        5        5        0
  R208        1012     1012     1012     1012     1012        0
  R212          30       30       30       30       30        0
  R219           5        5        5        5        5        0
  R222           5        5        5        5        5        0
  R226       21460    21460    21460    21460    21460        0
  R228       12024    12024    12024    12024    12024        0
  R246          30       30       30       30       30        0
  R252          50       50       50       50       50        0
  R256       20848     4816    58032    17360    51872    53216  <-- CHANGING
  R257       18599    18611    18600    18592    18595       19  <-- CHANGING
  R258        1479    30130    44621    36717    19098    43142  <-- CHANGING
  R259       18737    18749    18738    18729    18733       20  <-- CHANGING
  R260       27252    61244    14308    19964    23608    46936  <-- CHANGING
  R261       18752    18765    18754    18744    18748       21  <-- CHANGING
  R262        1972    40173    15804     5265     3619    38201  <-- CHANGING
  R263       18156    18172    18158    18146    18151       26  <-- CHANGING
  R264       20888    20888    20888    20888    20888        0
  R266       17141    53645    43853    60773     6189    54584  <-- CHANGING
  R267       17942    17952    17943    17935    17939       17  <-- CHANGING
  R270       27426    42916    43950    18594    23010    25356  <-- CHANGING
  R271       17904    17920    17906    17894    17899       26  <-- CHANGING
  R293       31730    31730    31730    31730    31730        0
  R297          80       80       80       80       80        0
  R298          80       80       80       80       80        0
  R299          95       95       95       95       95        0
  R301          80       80       80       80       80        0
  R303       32767    32767    32767    32767    32767        0
  R304          10       10       10       10       10        0
  R305       64776    64776    64776    64776    64776        0
  R306        4701     4701     4701     4701     4701        0
  R307       16331    16331    16331    16331    16331        0
  R308       58347    58347    58347    58347    58347        0
  R309       18167    18167    18167    18167    18167        0
  R313           5        5        5        5        5        0
  R316           5        5        5        5        5        0
  R370       18027    18022    18022    18027    18022        5  <-- CHANGING
  R380       49696    49696    49696    49696    49696        0
  R382          20       20       20       20       20        0
  R385          20       20       20       20       20        0
  R387       32767    32767    32767    32767    32767        0
  R388        3000     3000     3000     3000     3000        0
  R389       63921    63921    63921    63921    63921        0
  R392       17633    17633    17633    17633    17633        0
  R394          20       20       20       20       20        0
  R396       32767    32767    32767    32767    32767        0
  R397        3000     3000     3000     3000     3000        0
  R398       63922    63922    63922    63922    63922        0
  R400           3        3        3        3        3        0
  R417       32768    32768    32768    32768    32768        0
  R418       17312    17312    17312    17312    17312        0
  R431       16956    16952    16952    16956    16952        4  <-- CHANGING
  R432       19808    19816    19824    19832    19832       24  <-- CHANGING
  R433       49152    53248    57344    61440    61440    12288  <-- CHANGING
  R434       18074    18074    18074    18074    18074        0
  R435       30409    34603    38797    42992    42992    12583  <-- CHANGING
  R436       16158    16158    16158    16158    16158        0
  R437       33424    38175    42926    47678    47678    14254  <-- CHANGING
  R438       17203    17203    17203    17203    17203        0
  R439       33424    38175    42926    47678    47678    14254  <-- CHANGING
  R440       17163    17163    17163    17163    17163        0
  R442           5        5        5        5        5        0
  R478        5000     5000     5000     5000     5000        0
  R483       32767    32767    32767    32767    32767        0
  R484         300      300      300      300      300        0
  R485       63921    63921    63921    63921    63921        0
  R498       43008    43008    43008    43008    43008        0
  R499       18087    18087    18087    18087    18087        0
  R502        2068     8508    23576    56629    52782    54561  <-- CHANGING
  R503       17892    17927    17908    17912    17916       35  <-- CHANGING
  R505       17866    17904    17881    17885    17888       38  <-- CHANGING
  R506        6480     7680     6944     7072     7184     1200  <-- CHANGING
  R507       33051    24441    14705    54842    28597    40137  <-- CHANGING
  R508       15977    16010    15994    15998    16001       33  <-- CHANGING
  R511       23593    49807    13631    19923    58196    44565  <-- CHANGING
  R512       15951    15989    15966    15970    15973       38  <-- CHANGING
  R517        3000     3000     3000     3000     3000        0
  R520       17530    17530    17530    17530    17530        0
  R522       17633    17633    17633    17633    17633        0
  R523       16384    16384    16384    16384    16384        0
  R524       17564    17564    17564    17564    17564        0
  R525       16384    16384    16384    16384    16384        0
  R526       17564    17564    17564    17564    17564        0
  R528       17633    17633    17633    17633    17633        0
  R530       17549    17549    17549    17549    17549        0
  R532       17633    17633    17633    17633    17633        0
  R534          50       50       50       50       50        0
  R537         500      500      500      500      500        0
  R540          10       10       10       10       10        0
  R543          10       10       10       10       10        0
  R546          80       80       80       80       80        0
  R549          80       80       80       80       80        0
  R552          80       80       80       80       80        0
  R555          80       80       80       80       80        0
  R558          80       80       80       80       80        0
  R561          80       80       80       80       80        0
  R564          80       80       80       80       80        0
  R567          80       80       80       80       80        0
  R570          80       80       80       80       80        0
  R573          80       80       80       80       80        0
  R576          80       80       80       80       80        0
  R579          80       80       80       80       80        0
  R582          80       80       80       80       80        0
  R585          80       80       80       80       80        0
  R588          80       80       80       80       80        0
  R591          80       80       80       80       80        0
  R594          80       80       80       80       80        0
  R597          80       80       80       80       80        0
  R599          15       15       15       15       15        0
  R607       64325    26958    11466     7618    42916    56707  <-- CHANGING
  R608       17904    17927    17911    17915    17920       23  <-- CHANGING
  R610          80       80       80       80       80        0
  R613          80       80       80       80       80        0
  R616          80       80       80       80       80        0
  R619          80       80       80       80       80        0
  R622          80       80       80       80       80        0
  R625          80       80       80       80       80        0
  R628          80       80       80       80       80        0
  R632       49152    49152    49152    49152    49152        0
  R633       18138    18138    18138    18138    18138        0
  R642       40960    40960    40960    40960    40960        0
  R643       18060    18060    18060    18060    18060        0
  R644        3500     3500     3500     3500     3500        0
  R646          80       80       80       80       80        0
  R649          80       80       80       80       80        0
  R655        3500     3500     3500     3500     3500        0
  R656        1000     1000     1000     1000     1000        0
  R658          50       50       50       50       50        0
  R659       65486    65486    65486    65486    65486        0
  R677         100      100      100      100      100        0
  R679          50       50       50       50       50        0
  R682          50       50       50       50       50        0
  R685          30       30       30       30       30        0
  R688          30       30       30       30       30        0
  R691          10       10       10       10       10        0
  R693           3        3        3        3        3        0
  R694           3        3        3        3        3        0
  R695          24       24       24       24       24        0
  R697          30       30       30       30       30        0
  R700          30       30       30       30       30        0
  R703          30       30       30       30       30        0
  R706          30       30       30       30       30        0
  R709          30       30       30       30       30        0
  R712          30       30       30       30       30        0
  R715          30       30       30       30       30        0
  R718          30       30       30       30       30        0
  R721          30       30       30       30       30        0
  R724          30       30       30       30       30        0
  R727          30       30       30       30       30        0
  R730          30       30       30       30       30        0
  R863       16822    16822    16822    16822    16822        0
  R864          50       50       50       50       50        0
  R865          50       50       50       50       50        0
  R866         344      344      344      344      344        0
  R867       16202    16202    16202    16202    16202        0
  R883           2        2        2        2        2        0
  R884           2        2        2        2        2        0
  R885        1503     1503     1503     1503     1503        0
  R897       65411    65411    65411    65411    65411        0
  R898       17434    17434    17434    17434    17434        0
  R899       16202    16202    16202    16202    16202        0
  R925        3000     3000     3000     3000     3000        0
  R928           4        4        4        4        4        0
  R931       32767    32767    32767    32767    32767        0
  R932           5        5        5        5        5        0
  R933       64500    64500    64500    64500    64500        0
  R934           8        8        8        8        8        0
  R935           8        8        8        8        8        0
  R936         151      151      151      151      151        0
  R1151          4        4        4        4        4        0
  R1153         30       30       30       30       30        0
  R1154         30       30       30       30       30        0
  R1155      65506    65506    65506    65506    65506        0
  R1156      65507    65507    65507    65507    65507        0
  R1157          3        3        3        3        3        0
  R1158      65535    65535    65535    65535    65535        0
  R1159         31       31       31       31       31        0
  R1160          1        1        1        1        1        0
  R1162        100      100      100      100      100        0
  R1236          8        8        8        8        8        0
  R1238          5        5        5        5        5        0
  R1239       6144     6144     6144     6144     6144        0
  R1241          2        2        2        2        2        0
  R1271      40960    40960    40960    40960    40960        0
  R1272      18060    18060    18060    18060    18060        0
  R1371      20682    20682    20682    20682    20682        0
  R1372      16822    16822    16822    16822    16822        0
  R1449         80       80       80       80       80        0
  R1452         80       80       80       80       80        0
  R2000      20968    20968    20968    20968    20968        0
  R2002       4694     4694     4694     4694     4694        0
  R2003      20968    20968    20968    20968    20968        0
  R2004       2348     2348     2348     2348     2348        0
  R2005      20968    20968    20968    20968    20968        0
  R2007      21192    21192    21192    21192    21192        0
  R2008         78       78       78       78       78        0
  R2009      20968    20968    20968    20968    20968        0
  R2010      62526    62526    62526    62526    62526        0
  R2011         15       15       15       15       15        0
  R2012       5850     5850     5850     5850     5850        0
  R2027          1        1        1        1        1        0
  R2028          1        1        1        1        1        0
  R2029         65       65       65       65       65        0
  R2031       6000     6000     6000     6000     6000        0
  R2032         10       10       10       10       10        0
  R2033      13700    13700    13700    13700    13700        0
  R2034      15151    15151    15151    15151    15151        0
  R2035      32000    32000    32000    32000    32000        0
  R2036      12700    12700    12700    12700    12700        0
  R2037       1806     1806     1806     1806     1806        0
  R2038      15451    15451    15451    15451    15451        0
  R2039      13420    13420    13420    13420    13420        0
  R2040      15351    15351    15351    15351    15351        0
  R2042      20682    20682    20682    20682    20682        0
  R2046        108      108      108      108      108        0
  R2047      15451    15451    15451    15451    15451        0
  R2049        500      500      500      500      500        0
  R2051      32000    32000    32000    32000    32000        0
  R2054          2        2        2        2        2        0
  R2056         12       12       12       12       12        0
  R2057      65524    65524    65524    65524    65524        0
  R2058       1000     1000     1000     1000     1000        0
  R2060       1000     1000     1000     1000     1000        0
  R2062       5000     5000     5000     5000     5000        0
  R2063        500      500      500      500      500        0
  R2066        500      500      500      500      500        0
  R2067          4        4        4        4        4        0
  R2069        500      500      500      500      500        0
  R2070      27801     4106    12542    20828    29300    25194  <-- CHANGING
  R2071        500      500      500      500      500        0
  R2073      47536    10176    25344    47840    29792    37664  <-- CHANGING
  R2074       4249      634     1921     3185     4478     3844  <-- CHANGING
  R2075        500      500      500      500      500        0
  R2076       6186     6195     6205     6214     6223       37  <-- CHANGING
  R2077         16       16       16       16       16        0
  R2078       4804     6980      209     1403     4434     6771  <-- CHANGING
  R2096          2        2        2        2        2        0
  R2100        800      800      800      800      800        0
  R2102       2000     2000     2000     2000     2000        0
  R2104      16822    16822    16822    16822    16822        0
  R2105      13000    13000    13000    13000    13000        0
  R2107          1        1        1        1        1        0
  R2108      13000    13000    13000    13000    13000        0
  R2110      60842    60842    60842    60842    60842        0
  R2111      13000    13000    13000    13000    13000        0
  R2112      60934    60934    60934    60934    60934        0
  R2113      13000    13000    13000    13000    13000        0
  R2115       8768     8768     8768     8768     8768        0
  R2116        187      187      187      187      187        0
  R2117      13000    13000    13000    13000    13000        0
  R2118      62510    62510    62510    62510    62510        0
  R2119         15       15       15       15       15        0
  R2120       8932     8932     8932     8932     8932        0
  R2136          5        5        5        5        5        0
  R2137      49152    49152    49152    49152    49152        0
  R2139         55       55       55       55       55        0
  R2140         55       55       55       55       55        0
  R2141        891      891      891      891      891        0
  R2143          5        5        5        5        5        0
  R2144      49152    49152    49152    49152    49152        0
  R2145      13400    13400    13400    13400    13400        0
  R2147         50       50       50       50       50        0
  R2149      12600    12600    12600    12600    12600        0
  R2150      32000    32000    32000    32000    32000        0
  R2152         25       25       25       25       25        0
  R2153         25       25       25       25       25        0
  R2154        514      514      514      514      514        0
  R2194      17588    17588    17588    17588    17588        0
  R2196      17588    17588    17588    17588    17588        0
  R2197          7        7        7        7        7        0
  R2198          7        7        7        7        7        0
  R2199        102      102      102      102      102        0
  R3400      28160    28160    28160    28160    28160        0
  R3401      18085    18085    18085    18085    18085        0
  R3402      22528    22528    22528    22528    22528        0
  R3403      18052    18052    18052    18052    18052        0
  R3404      17434    17434    17434    17434    17434        0
  R3405      13816    13816    13816    13816    13816        0
  R3407      13000    13000    13000    13000    13000        0
  R3408      13312    13312    13312    13312    13312        0
  R3409      18056    18056    18056    18056    18056        0
  R3410      13312    13312    13312    13312    13312        0
  R3411      18056    18056    18056    18056    18056        0
  R3412      56320    56320    56320    56320    56320        0
  R3413      18031    18031    18031    18031    18031        0
  R3414      57241    57241    57241    57241    57241        0
  R3415      18007    18007    18007    18007    18007        0
  R3420       8192     8192     8192     8192     8192        0
  R3421      17995    17995    17995    17995    17995        0
  R3422       8192     8192     8192     8192     8192        0
  R3423      17995    17995    17995    17995    17995        0
  R3425          5        5        5        5        5        0
  R3427      28672    28672    28672    28672    28672        0
  R3428      17990    17990    17990    17990    17990        0
  R3430         45       47       46       46       47        2  <-- CHANGING
  R3437      49152    49152    49152    49152    49152        0
  R3438      17950    17950    17950    17950    17950        0
  R3439      10160    10160    10160    10160    10160        0
  R3441      28672    28672    28672    28672    28672        0
  R3442      18069    18069    18069    18069    18069        0
  R3445          3        3        3        3        3        0
  R3446       2026     2026     2026     2026     2026        0
  R3448          2        2        2        2        2        0
  R3449          2        2        2        2        2        0
  R3450         17       17       17       17       17        0
  R3451         44       44       44       44       44        0
  R3452          5        5        5        5        5        0
  R3453          5        5        5        5        5        0
  R3454       1635     1635     1635     1635     1635        0
  R3457         10       10       10       10       10        0
  R3460         10       10       10       10       10        0
  R3468      18434    18434    18434    18434    18434        0
  R3469      13608    13608    13608    13608    13608        0
  R3470          5        5        5        5        5        0
  R3471          5        5        5        5        5        0
  R3472       1635     1635     1635     1635     1635        0
  R3473      32767    32767    32767    32767    32767        0
  R3474         15       15       15       15       15        0
  R3475      64023    64023    64023    64023    64023        0
  R3477         15       15       15       15       15        0
  R3479      20480    20480    20480    20480    20480        0
  R3480      17724    17724    17724    17724    17724        0
  R3494          2        2        2        2        2        0
  R3495      65534    65534    65534    65534    65534        0
  R3496       1000     1000     1000     1000     1000        0
  R3500       9845     9845     9845     9845     9845        0
  R3501      16822    16822    16822    16822    16822        0
  R3514         21       21       21       21       21        0
  R3516        517      517      517      517      517        0
  R3531      16822    16822    16822    16822    16822        0
  R3532      16783    16783    16783    16783    16783        0
  R3533      16783    16783    16783    16783    16783        0
  R3534      16822    16822    16822    16822    16822        0
  R3535      13000    13000    13000    13000    13000        0
  R3536      16046    16046    16046    16046    16046        0
  R3537      18668    18668    18668    18668    18668        0
  R3538      19062    19062    19062    19062    19062        0
  R3539      23642     5414    15443    22507    15443    18228  <-- CHANGING
  R3540      16859    16830    16833    16841    16833       29  <-- CHANGING
  R3541         45       45       45       45       45        0
  R3542      20968    20968    20968    20968    20968        0
  R3543       4251     4251     4251     4251     4251        0
  R3544          5        5        5        5        5        0
  R3545      26624    26624    26624    26624    26624        0
  R3547          3        3        3        3        3        0
  R3548       6144     6144     6144     6144     6144        0
  R3549      15351    15351    15351    15351    15351        0
  R3550      12700    12700    12700    12700    12700        0
  R3552       6656     6656     6656     6656     6656        0
  R3553      18093    18093    18093    18093    18093        0
  R3554      21155    21155    21155    21155    21155        0
  R3558      36396    31708    40751    37848    28804    11947  <-- CHANGING
  R3559      16950    16956    16953    16951    16954        6  <-- CHANGING
  R3560      20480    20480    20480    20480    20480        0
  R3561      18115    18115    18115    18115    18115        0
  R3584      28996     5283    13704    22004    30481    25198  <-- CHANGING
  R3585      65520    65520    65520    65520    65520        0
  R3586      64377    64377    64377    64377    64377        0
  R3587         11       11       11       11       11        0
  R3588         15       15       15       15       15        0
  R3589      28996     5283    13704    22004    30481    25198  <-- CHANGING
  R3590       9937     9144     8801     8711     8711     1226  <-- CHANGING
  R3591      28996     5283    13704    22004    30481    25198  <-- CHANGING
  R5106       2413     2413     2413     2413     2413        0
  R5108      64536    64536    64536    64536    64536        0
  R5230          2        2        2        2        2        0
  R5231         24       24       24       24       24        0
  R5232       2048     2048     2048     2048     2048        0
  R5234      14340    14432    14525    14617    14710      370  <-- CHANGING
  R5235      18000    18000    18000    18000    18000        0
  R5236      45933    43388    43667    36107    35248    10685  <-- CHANGING
  R5238      18000    18000    18000    18000    18000        0
  R5240         24       24       24       24       24        0
  R5241         24       24       24       24       24        0
  R5242      18432    18432    18432    18432    18432        0
  R5244      18000    18000    18000    18000    18000        0
  R5247         24       24       24       24       24        0
  R5248       2048     2048     2048     2048     2048        0
  R5249          7        7        7        7        7        0
  R5250          7        7        7        7        7        0
  R5251      18432    18432    18432    18432    18432        0
  R5253      18000    18000    18000    18000    18000        0
  R5256          2        2        2        2        2        0
  R5257       2048     2048     2048     2048     2048        0
  R5259         24       24       24       24       24        0
  R5260       2048     2048     2048     2048     2048        0
  R5261         30       30       30       30       30        0
  R5262         30       30       30       30       30        0
  R5263      18432    18432    18432    18432    18432        0
  R5265      18000    18000    18000    18000    18000        0
  R5268          2        2        2        2        2        0
  R5269       2048     2048     2048     2048     2048        0
  R5271         24       24       24       24       24        0
  R5272       2048     2048     2048     2048     2048        0
  R5273        120      120      120      120      120        0
  R5274        120      120      120      120      120        0
  R5275      18432    18432    18432    18432    18432        0
  R5276      14357    14449    14542    14634    14727      370  <-- CHANGING
  R5277      18000    18000    18000    18000    18000        0
  R5278      44238    41693    41972    34412    33553    10685  <-- CHANGING
  R5279          1        1        1        1        1        0
  R5280          2        2        2        2        2        0
  R5281       2048     2048     2048     2048     2048        0
  R5282          2        2        2        2        2        0
  R5283         24       24       24       24       24        0
  R5284       2048     2048     2048     2048     2048        0
  R5285        302      302      302      302      302        0
  R5286        500      500      500      500      500        0
  R5287       2048     2048     2048     2048     2048        0
  R5499       3411     3411     3411     3411     3411        0
  R5501        302      302      302      302      302        0
  R5505        140      140      140      140      140        0
  R5506        124      124      124      124      124        0
  R5519      16952    16948    16952    16952    16956        8  <-- CHANGING
  R5520      33074    31914    28092     9839    40203    30364  <-- CHANGING
  R5521      17959    17965    17945    17940    17942       25  <-- CHANGING
  R5522      65496    65496    65496    65496    65496        0
  R5528      32000    32000    32000    32000    32000        0
  R5529      52697     5247    40751    54148    28804    48901  <-- CHANGING
  R5530      16952    16947    16953    16953    16954        7  <-- CHANGING
  R5545          1        1        1        1        1        0
  R5549          3        3        3        3        3        0
  R5550          2        2        2        2        2        0
  R5551       2026     2026     2026     2026     2026        0
