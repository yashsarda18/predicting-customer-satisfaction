import numpy as np
import pandas as pd
from zenml import pipeline,step
from zenml.config import DockerSettings 
from zenml.constants import DEFAULT_SERVICE_START_STOP_TIMEOUT
from zenml.integrations.constants import MLFLOW
from zenml.integrations.mlflow.model_deployers.mlflow_model_deployer import MLFlowModelDeployer
import json
from zenml.integrations.mlflow.services import MLFlowDeploymentService
from zenml.integrations.mlflow.steps import mlflow_model_deployer_step
from zenml.steps import BaseParameters,Output
from .utils import get_data_for_test
from steps.clean_data import clean_df
from steps.evaluation import evaluate_model
from steps.ingest_data import ingest_df
from steps.model_train import train_model

docker_settings = DockerSettings(required_integrations=[MLFLOW])

class DeploymentTriggerConfig(BaseParameters):
    """
    Deployment Trigger Config
    """
    min_accuracy: float = 0 
    
@step(enable_cache=False)
def dynamic_importer() -> str:
    data = get_data_for_test()
    return data

@step
def deployment_trigger(
    accuracy: float,
    config: DeploymentTriggerConfig
):
    """
    Implements a simple model deployment trigger that looks at the input model accuracy and decides if it is good enough to deploy or not
    """
    return accuracy > config.min_accuracy

class MLFlowDeploymentLoaderStepParameters(BaseParameters):
    """
    MLFLow deployment  getter parameters

    Args:
        BaseParameters (_type_): _description_
    """
    pipeline_name: str
    step_name: str
    running: bool = True
    
@step(enable_cache=False)
def prediction_service_loader(
    pipeline_name: str,
    pipeline_step_name: str,
    running: bool = True,
    model_name: str = "model"
) -> MLFlowDeploymentService:
    """
    Get the prediction service started by the deployment pipeline

    Args:
        pipeline_name (str): _description_
        pipeline_step_name (str): _description_
        running (bool, optional): _description_. Defaults to True.
        model_name (str, optional): _description_. Defaults to "model".

    Returns:
        MLFlowDeploymentService: _description_
    """
    # Get the MLFlow deployer stack component
    mlflow_model_deployer_component = MLFlowModelDeployer.get_active_model_deployer()
    # Fetch existing services with the same pipeline name ,step name and model name
    existing_services = mlflow_model_deployer_component.find_model_server(
        pipeline_name=pipeline_name,
        pipeline_step_name=pipeline_step_name,
        model_name=model_name,
        running=running
    )
    
    if not existing_services:
        raise RuntimeError(
            f"No MLFlow Deployment service found for pipeline {pipeline_name}. "
            f"step {pipeline_step_name} and model {model_name}. "
            f"Pipeline for the '{model_name}' model is currently "
            f"running."
        )
    return existing_services[0]

@step
def predictor(
    service:MLFlowDeploymentService,
    data: str
) -> np.ndarray:
    """
    Predicts on the given data using the given service

    Args:
        service (MLFlowDeploymentService): _description_
        data (np.ndarray): _description_

    Returns:
        np.ndarray: _description_
    """
    service.start(timeout=10)  # should be a NOP if already started
    data = json.loads(data)
    data.pop("columns")
    data.pop("index")
    columns_for_df = [
        "payment_sequential",
        "payment_installments",
        "payment_value",
        "price",
        "freight_value",
        "product_name_lenght",
        "product_description_lenght",
        "product_photos_qty",
        "product_weight_g",
        "product_length_cm",
        "product_height_cm",
        "product_width_cm",
    ]
    df = pd.DataFrame(data["data"], columns=columns_for_df)
    json_list = json.loads(json.dumps(list(df.T.to_dict().values())))
    data = np.array(json_list)
    prediction = service.predict(data)
    return prediction

@pipeline(enable_cache=False,settings={"docker": docker_settings})
def continuous_deployment_pipeline(
    data_path: str,
    min_accuracy: float = 0,
    workers: int = 1,
    timeout: int = DEFAULT_SERVICE_START_STOP_TIMEOUT
):
    df = ingest_df(data_path=data_path)
    X_train, X_test, y_train, y_test = clean_df(df)
    model = train_model(X_train, X_test, y_train, y_test)
    r2_score,rmse = evaluate_model(model,X_test,y_test)
    deployment_decision = deployment_trigger(r2_score)
    mlflow_model_deployer_step(
        model=model,
        deploy_decision=deployment_decision,
        workers=workers,
        timeout=timeout
    )
    
@pipeline(enable_cache=False,settings={"docker": docker_settings})
def inference_pipeline(
    pipeline_name: str,
    pipeline_step_name: str
):
    data = dynamic_importer()
    service = prediction_service_loader(
        pipeline_name=pipeline_name,
        pipeline_step_name=pipeline_step_name,
        running=False
    )
    prediction = predictor(service=service,data=data)
    return prediction
