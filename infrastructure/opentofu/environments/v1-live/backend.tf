terraform {
  backend "s3" {}

  encryption {
    state {
      enforced = true
    }
    plan {
      enforced = true
    }
  }
}
