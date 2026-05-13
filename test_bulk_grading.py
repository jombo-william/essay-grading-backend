import asyncio
import time
from services.gemini_grader import grade_essay_with_gemini

# Sample essays for testing
essays = [
    {
        "title": "The Industrial Revolution",
        "text": "The industrial revolution was a transformative period that began in Great Britain during the late 18th century. It brought significant technological advancements, particularly the steam engine and mechanized manufacturing. These innovations led to the growth of factories, which fundamentally changed how goods were produced.",
        "instructions": "Write about the impact of the industrial revolution"
    },
    {
        "title": "Climate Change",
        "text": "Climate change is one of the most pressing issues of our time. Rising global temperatures, melting ice caps, and extreme weather events are all evidence of this phenomenon. Human activities, particularly the burning of fossil fuels, are the primary drivers.",
        "instructions": "Discuss the causes and effects of climate change"
    },
    {
        "title": "Artificial Intelligence",
        "text": "Artificial intelligence is revolutionizing various industries. From healthcare to transportation, AI systems are making processes more efficient and accurate. However, ethical concerns about privacy and job displacement remain significant challenges.",
        "instructions": "Analyze the benefits and risks of artificial intelligence"
    },
    {
        "title": "Renewable Energy",
        "text": "Solar and wind power are becoming increasingly cost-effective alternatives to fossil fuels. Many countries are setting ambitious targets for renewable energy adoption. This transition is crucial for reducing carbon emissions and combating climate change.",
        "instructions": "Evaluate the potential of renewable energy sources"
    },
    {
        "title": "Online Education",
        "text": "The COVID-19 pandemic accelerated the adoption of online learning platforms. While this mode of education offers flexibility and accessibility, it also presents challenges such as digital divide and reduced social interaction.",
        "instructions": "Assess the effectiveness of online education"
    },
    {
        "title": "Space Exploration",
        "text": "Space exploration has led to numerous technological innovations that benefit life on Earth. From satellite communications to medical devices, the spin-offs from space research are extensive. Future missions to Mars represent the next frontier.",
        "instructions": "Discuss the value of space exploration"
    },
    {
        "title": "Mental Health Awareness",
        "text": "Mental health is finally receiving the attention it deserves. Stigma around mental illness is gradually decreasing, and more people are seeking help. Workplace mental health programs are becoming standard practice.",
        "instructions": "Write about the importance of mental health awareness"
    },
    {
        "title": "Cybersecurity",
        "text": "As our lives become increasingly digital, cybersecurity threats are growing in sophistication. Data breaches can have devastating consequences for individuals and organizations. Strong passwords and two-factor authentication are essential protections.",
        "instructions": "Explain the importance of cybersecurity"
    },
    {
        "title": "Genetic Engineering",
        "text": "CRISPR technology has revolutionized genetic engineering. This tool allows scientists to edit DNA with unprecedented precision. While the medical applications are promising, ethical concerns about designer babies remain.",
        "instructions": "Analyze the implications of genetic engineering"
    },
    {
        "title": "Sustainable Agriculture",
        "text": "Feeding a growing global population while protecting the environment requires sustainable agricultural practices. Organic farming, crop rotation, and reduced pesticide use are all part of the solution.",
        "instructions": "Discuss sustainable farming methods"
    },
    {
        "title": "Urbanization",
        "text": "More than half of the world's population now lives in cities. Rapid urbanization brings both opportunities and challenges, including strain on infrastructure, housing shortages, and environmental degradation.",
        "instructions": "Write about the effects of urbanization"
    },
    {
        "title": "Blockchain Technology",
        "text": "Blockchain is more than just the technology behind cryptocurrencies. Its applications in supply chain management, voting systems, and digital identity verification are transforming various industries.",
        "instructions": "Explain blockchain technology and its uses"
    },
    {
        "title": "Vaccine Development",
        "text": "The rapid development of COVID-19 vaccines demonstrated the power of modern biotechnology. mRNA technology, in particular, shows promise for treating other diseases like cancer and HIV.",
        "instructions": "Discuss vaccine development and its importance"
    },
    {
        "title": "Plastic Pollution",
        "text": "Millions of tons of plastic waste enter our oceans each year, harming marine life and ecosystems. Solutions include reducing single-use plastics, improving recycling, and developing biodegradable alternatives.",
        "instructions": "Address the problem of plastic pollution"
    },
    {
        "title": "Remote Work",
        "text": "The shift to remote work has changed traditional office culture. While many employees appreciate the flexibility, companies face challenges in maintaining team cohesion and corporate culture.",
        "instructions": "Analyze the future of remote work"
    },
    {
        "title": "Nuclear Energy",
        "text": "Nuclear power provides a reliable, low-carbon energy source. However, concerns about waste disposal and reactor safety continue to limit its adoption. New reactor designs aim to address these issues.",
        "instructions": "Evaluate nuclear energy as a power source"
    },
    {
        "title": "Social Media Impact",
        "text": "Social media platforms have transformed how we communicate and consume information. While they enable global connections, they also contribute to misinformation, echo chambers, and mental health issues.",
        "instructions": "Discuss the societal impact of social media"
    },
    {
        "title": "Electric Vehicles",
        "text": "The transition to electric vehicles is accelerating as battery technology improves and charging infrastructure expands. However, challenges remain regarding range anxiety, charging time, and battery disposal.",
        "instructions": "Evaluate the future of electric vehicles"
    },
    {
        "title": "Water Scarcity",
        "text": "Climate change and population growth are exacerbating water scarcity in many regions. Solutions include desalination, water recycling, and improved irrigation techniques.",
        "instructions": "Write about global water scarcity"
    },
    {
        "title": "Quantum Computing",
        "text": "Quantum computers have the potential to solve problems that are impossible for classical computers. Applications include drug discovery, financial modeling, and cryptography. However, practical quantum computers are still years away.",
        "instructions": "Explain quantum computing fundamentals"
    }
]

rubric = {"content": 40, "structure": 30, "grammar": 20, "evidence": 10}
max_score = 100

async def grade_single_essay(essay, index):
    print(f"  [{index + 1}] Grading: {essay['title']}...")
    start = time.time()
    
    result = grade_essay_with_gemini(
        essay_text=essay['text'],
        instructions=essay['instructions'],
        reference_material="",
        rubric=rubric,
        max_score=max_score
    )
    
    duration = time.time() - start
    return {
        "index": index + 1,
        "title": essay['title'],
        "score": result['score'],
        "duration": round(duration, 2),
        "feedback_length": len(result['feedback'])
    }

async def run_bulk_test():
    print("=" * 70)
    print("📝 BULK ESSAY GRADING TEST - Gemini 2.5 Flash")
    print("=" * 70)
    print(f"\n📚 Total essays to grade: {len(essays)}")
    print("🤖 Starting AI grading...\n")
    
    start_time = time.time()
    
    # Process all essays concurrently
    tasks = [grade_single_essay(essay, i) for i, essay in enumerate(essays)]
    results = await asyncio.gather(*tasks)
    
    total_time = time.time() - start_time
    
    print("\n" + "=" * 70)
    print("📊 RESULTS SUMMARY")
    print("=" * 70)
    
    scores = [r['score'] for r in results]
    durations = [r['duration'] for r in results]
    
    print(f"\n✅ Total essays graded: {len(results)}")
    print(f"⏱️  Total time: {total_time:.2f} seconds")
    print(f"📈 Average time per essay: {sum(durations)/len(durations):.2f} seconds")
    print(f"🚀 Fastest: {min(durations):.2f}s | Slowest: {max(durations):.2f}s")
    print(f"🎯 Score range: {min(scores)} - {max(scores)}")
    print(f"⭐ Average score: {sum(scores)/len(scores):.1f}/100")
    
    print("\n" + "-" * 70)
    print("📋 DETAILED RESULTS")
    print("-" * 70)
    
    for r in results:
        print(f"  [{r['index']:2d}] {r['title'][:30]:30s} → Score: {r['score']:3d}/100 (took {r['duration']}s)")
    
    print("\n" + "=" * 70)
    print("✅ Bulk grading test completed!")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(run_bulk_test())
