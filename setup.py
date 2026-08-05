from setuptools import setup, find_packages

setup(
    name="mcp-domain-availability",
    version="0.1.0",
    description="MCP server for checking domain availability across multiple TLDs",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="Ivan Luna",
    author_email="ivan@imprvhub.com",
    url="https://github.com/imprvhub/mcp-domain-availability",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    python_requires=">=3.10",
    install_requires=[
        "mcp[cli]>=1.3.0,<2.0.0",
        "requests>=2.32.3", 
        "aiohttp>=3.11.13",
        "dnspython>=2.4.0",
        "whois>=0.9.27",
    ],
    entry_points={
        "console_scripts": [
            "mcp-domain-availability=mcp_domain_availability.main:mcp.run",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
)
