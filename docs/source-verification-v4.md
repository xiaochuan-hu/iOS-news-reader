# V4 public source verification

Manually checked on 2026-08-19 with redirected GET requests, then parsed locally with feedparser.
These checks are development evidence only; automated tests never access the public internet.

| Source | Category | Endpoint | Result |
|---|---|---|---|
| OpenAI News | ai_llm_agent | https://openai.com/news/rss.xml | HTTP 200 XML; 1,139/1,139 entries had parseable timestamps |
| Google DeepMind | ai_llm_agent | https://deepmind.google/blog/rss.xml | HTTP 200 XML; 100/100 entries timestamped |
| Hugging Face Blog | ai_llm_agent | https://huggingface.co/blog/feed.xml | HTTP 200 RSS; 844/844 entries timestamped |
| MIT Technology Review | technology | https://www.technologyreview.com/feed/ | HTTP 200 RSS; 10/10 entries timestamped |
| arXiv Artificial Intelligence | ai_llm_agent | https://rss.arxiv.org/rss/cs.AI | HTTP 200 RSS; 275/275 entries timestamped |
| arXiv Robotics | robotics | https://rss.arxiv.org/rss/cs.RO | HTTP 200 RSS; 69/69 entries timestamped |
| NVIDIA Robotics | embodied_ai | https://developer.nvidia.com/blog/category/robotics/feed/ | HTTP 200 Atom; 100/100 entries timestamped |
| IEEE Spectrum Robotics | robotics | https://spectrum.ieee.org/feeds/topic/robotics.rss | HTTP 200 RSS; 30/30 entries timestamped |
| GitHub Trending Today | separate ranking snapshot | https://github.com/trending?since=daily | HTTP 200 HTML; production parser returned ordered ranks 1–10 |

The Robot Report feed candidate returned HTTP 403 and no parseable entries, NVIDIA's generic AI
category candidate returned an empty feed, and the Boston Dynamics candidate returned HTTP 404.
They were not added. A valid endpoint can still contribute zero items on a quiet day because V4
never widens its strict 24-hour news window.
