# Team meetings 2026-11-15 (one block per meeting; full minutes on the VPS in private/marketing/)

## 01:13 | shop ALL 0.79 | revenue 4390858688 | levers moved: pricing.price_band: [0.72, 0.76] -> [0.72, 0.74], follow_skus: +['G00327e80', 'G05277ffe'] -[]
HR: the shop ALL percentile is 0.848 against a target of 0.864. It was missed because code paths (slow clear, group buy, the cost ladder) still sell under cost (6 in the last hour), which hurts Book at weight 0.5, and because goods at 0.76 convert only 12%, which caps revenue. This run I change three levers: price band 0.72-0.76 to 0.72-0.74 (arm data 50% against 12%), the restock picks (finance's

## 04:29 | shop ALL 0.794 | revenue 4415640696 | levers moved: follow_skus: +['G3e0d6c67', 'G6cd0e9d4'] -[]
HR: the shop ALL percentile is 0.848 against a target of 0.864. It was missed because code paths still sell under cost (3 in the last hour, 17 of 52 sales under 5% margin), which drains Book (weight 0.5), and because overnight revenue fell to 7.7M/h. This run I change two levers. First, follow_skus adds G6cd0e9d4 and G3e0d6c67, held units with a wide margin under 0.98 x the median, to turn held st

## 07:17 | shop ALL 0.791 | revenue 4415006188 | levers moved: pricing.price_band: [0.72, 0.74] -> [0.74, 0.75]
HR: the shop ALL percentile is 0.862 against a target of 0.877. It was missed because deals collapsed to 4 per hour overnight, worth shrank by about 0.32M per hour through under-cost markdowns (G0be9ed07 at 29,128 against a 29,938 cost), and wording-based blocks rejected regular buyer shops. This run I stop goods-72-74 with B (0.74) as the winner, at 5x the profit per quote and higher conversion, 

## 10:16 | shop ALL 0.79 | revenue 4419086635 | levers moved: follow_skus: +['G2af689a3', 'G445ada94', 'G85a1a856', 'G8ec51935'] -[]
HR: the shop ALL percentile is 0.864 against a 0.879 target. It was missed because overnight deals fell to 4-6.5 per hour, the shop card timed out so buyers could not find us, block_buyers turned away regular customers, and 71% of big-goods quotes sat at or over the grade median, the point where conversion collapses. This run I keep the pricing levers, following lessons 0, 3 and 6 (12% floors, 3 h

## 13:25 | shop ALL 0.787 | revenue 4445960166 | levers moved: follow_skus: +['G0667d440', 'G0be9ed07', 'G399e5df1'] -['G07ec17d5', 'G1515d951', 'G6b9db161']
HR: the shop ALL percentile is 0.864 against a 0.879 target. It was missed because sales margin fell to 7-8%: code paths still sell under cost (audit red G476cc094), and follow asks for big goods land over the median, where they do not convert. This run I stop both finished experiments with their winners: food at 0.72, and a 12% goods floor, which earned 2.6x the profit per quote of 18%. I start t

## 15:56 | shop ALL 0.785 | revenue 4484665353 | levers moved: exclusive_skus: +['G8aa8f196'] -[], follow_skus: +['G021aae64'] -['G30604071']
HR: the shop ALL percentile is 0.864 against a 0.879 target. It was missed because margin is thin (24 under-cost sales in the last hour and 58 of 177 under 5%). The cause is code paths ('food at market' quotes at cost +1, and the floor uses average cost instead of the shipped unit's cost), not the price band. This run I file a single final unit-cost floor, stop both finished experiments with the h

## 18:58 | shop ALL 0.782 | revenue 4530362499 | levers moved: pricing.price_band: [0.74, 0.75] -> [0.72, 0.75]
HR: the shop ALL percentile is 0.864 against a 0.879 target. It was missed because profit halved to 1.28M/h and audit Book went -44.7k/h. Quotes are landing under the shipped unit's cost (G19a70f63, G0be9ed07, G7a341425, G1324bb95), and the 15% test arm actually earned 1.6% because overrides bypass the floor. This run, I stop both experiments with the profitable A arms (food 0.72, goods 12%, follo

## 22:01 | shop ALL 0.782 | revenue 4599132025 | levers moved: follow_skus: +['G24f5f979', 'G4a12722c', 'Gbec2b68f', 'Gf6b70da8'] -[]

