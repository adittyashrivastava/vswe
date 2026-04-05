from setuptools import setup, find_packages

setup(
    name="vswe-checkpoint",
    version="0.1.0",
    description="ML checkpoint manager for VSWE training jobs on AWS Spot instances",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "boto3>=1.28",
    ],
    extras_require={
        "torch": ["torch>=2.0"],
        "dev": [
            "pytest",
            "pytest-cov",
            "moto[dynamodb,s3]",
        ],
    },
)
